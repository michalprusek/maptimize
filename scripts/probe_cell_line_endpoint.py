"""Call the cell-line write endpoints against the REAL database, once.

CLAUDE.md's rule: a mocked session models no attribute expiry, so the
`MissingGreenlet` class of bug (serialising `updated_at` after an UPDATE) is
invisible to every unit test and shows up only as a 500 in production. Every new
write endpoint gets one run of this.

⚠️ Each handler call gets its OWN session, because that is what a real request
gets from `get_db()`. Sharing one session is not a shortcut: `async_session_maker`
has `expire_on_commit=False`, so it keeps relations loaded by an earlier call and
the probe then reports failures the application does not have.

Writes only to an experiment this script creates and deletes. Nothing belonging
to anyone else is touched.

    docker exec -i -w /app maptimize-backend python - < scripts/probe_cell_line_endpoint.py
"""
import asyncio

from sqlalchemy import select

from database import async_session_maker
from models.cell_line import CellLine
from models.experiment import Experiment
from models.user import User
from routers import cell_lines as cl_router
from routers import experiments as exp_router
from schemas.cell_line import CellLineCreate
from schemas.experiment import ExperimentCreate

PROBE_NAME = "__probe_cell_line__"
PROBE_LINE = "__probe_line__"

failures = []


def check(label, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


async def call(fn, *args, **kwargs):
    """One handler call, one session -- exactly what a real request gets."""
    async with async_session_maker() as db:
        return await fn(*args, db=db, **kwargs)


async def main():
    async with async_session_maker() as db:
        user = (await db.execute(
            select(User).where(User.email == "12bprusek@gym-nymburk.cz")
        )).scalar_one()
        u2os = (await db.execute(
            select(CellLine).where(CellLine.name == "U2OS")
        )).scalar_one_or_none()
    print(f"user={user.id}  U2OS={'id ' + str(u2os.id) if u2os else 'MISSING'}")
    check("the seeded U2OS row exists", u2os is not None)
    if u2os is None:
        return

    # -- create a throwaway experiment carrying the line from the start -------
    created = await call(
        exp_router.create_experiment,
        ExperimentCreate(name=PROBE_NAME, cell_line_id=u2os.id),
        current_user=user,
    )
    check("create_experiment returns the cell line", created.cell_line is not None,
          f"got {created.cell_line}")
    exp_id = created.id

    try:
        # -- assign a second line ---------------------------------------------
        line = await call(
            cl_router.create_cell_line, CellLineCreate(name=PROBE_LINE),
            current_user=user,
        )
        check("create_cell_line auto-picks a colour", bool(line.color), str(line.color))

        # The case-folded uniqueness check, against a real Postgres rather than a
        # mocked statement -- func.lower() has to reach the database.
        try:
            await call(
                cl_router.create_cell_line,
                CellLineCreate(name=f"  {PROBE_LINE.upper()}  "),
                current_user=user,
            )
            check("a case variant is refused", False, "it was created")
        except Exception as exc:
            check("a case variant is refused", getattr(exc, "status_code", None) == 400,
                  repr(exc)[:90])

        out = await call(
            exp_router.update_experiment_cell_line, exp_id,
            cell_line_id=line.id, current_user=user,
        )
        # THE reason this probe exists: serialising the response after an UPDATE.
        check("assign returns without MissingGreenlet",
              out.cell_line is not None and out.cell_line.id == line.id, str(out.cell_line))
        check("assign refreshed updated_at", out.updated_at is not None)

        # -- clear it (the branch that skips the existence lookup) ------------
        out = await call(
            exp_router.update_experiment_cell_line, exp_id,
            cell_line_id=None, current_user=user,
        )
        check("clearing returns cell_line=None", out.cell_line is None, str(out.cell_line))

        # -- re-assign the value the row previously held ----------------------
        out = await call(
            exp_router.update_experiment_cell_line, exp_id,
            cell_line_id=u2os.id, current_user=user,
        )
        check("re-assigning after a clear sticks",
              out.cell_line is not None and out.cell_line.id == u2os.id, str(out.cell_line))

        # -- a stale id must 404, not write NULL ------------------------------
        try:
            await call(
                exp_router.update_experiment_cell_line, exp_id,
                cell_line_id=99_999, current_user=user,
            )
            check("a stale cell line id 404s", False, "it was accepted")
        except Exception as exc:
            check("a stale cell line id 404s", getattr(exc, "status_code", None) == 404,
                  repr(exc)[:90])

        # -- delete refused while referenced ----------------------------------
        await call(exp_router.update_experiment_cell_line, exp_id,
                   cell_line_id=line.id, current_user=user)
        try:
            await call(cl_router.delete_cell_line, line.id, current_user=user)
            check("delete refused while an experiment references it", False, "it was deleted")
        except Exception as exc:
            check("delete refused while an experiment references it",
                  getattr(exc, "status_code", None) == 409, repr(exc)[:90])
    finally:
        # -- clean up: the experiment, then the line it was holding -----------
        async with async_session_maker() as db:
            exp = (await db.execute(
                select(Experiment).where(Experiment.name == PROBE_NAME)
            )).scalar_one_or_none()
            if exp:
                await db.delete(exp)
                await db.commit()
        async with async_session_maker() as db:
            for row in (await db.execute(
                select(CellLine).where(CellLine.name.in_([PROBE_LINE, PROBE_LINE.upper()]))
            )).scalars().all():
                await db.delete(row)
            await db.commit()
        async with async_session_maker() as db:
            left = (await db.execute(
                select(Experiment).where(Experiment.name == PROBE_NAME)
            )).scalar_one_or_none()
            lines = (await db.execute(
                select(CellLine).where(CellLine.name.like("__probe%"))
            )).scalars().all()
        check("probe rows cleaned up", left is None and not lines,
              f"experiment={left} lines={[c.name for c in lines]}")

    print()
    print("ALL OK" if not failures else f"FAILURES: {failures}")


asyncio.run(main())
