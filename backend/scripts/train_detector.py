"""Retrain the cell detector on the crops the lab has curated.

    python scripts/train_detector.py build    --out /work/ds
    python scripts/train_detector.py train    --data /work/ds --init yolo11s.pt --name y11s_640
    python scripts/train_detector.py evaluate --data /work/ds \\
        --weights old=/app/weights/best.pt new=/work/ds/runs/y11s_640/weights/best.pt

``build`` reads every FOV and its crops (read-only), classifies where each FOV's
boxes came from (``ml/detection/training.py`` explains why that matters), splits
whole experiments into train / val / test and writes a YOLO dataset. Training
images go through ``normalize_image`` + ``model_input`` — the functions
production detection calls — so the network trains on the pixels it will see.

``evaluate`` runs every model through ``CellDetector.detect``, the production
call, picks the confidence threshold on ``val`` and reports ``test``, with a
paired bootstrap over test experiments against the first model listed.

Deploying is a separate, deliberate step: copy the chosen ``best.pt`` over
``weights/best.pt`` (keep the old file), set ``CONF_THRESHOLD`` in
``ml/detection/detector.py`` to the threshold ``evaluate`` chose, rebuild.

Run it inside the backend image, on the GPU, as the host user:

    docker run --rm --gpus all --ipc=host --user 1000:1000 \\
        --network container:maptimize-backend \\
        --env-file <(docker exec maptimize-backend env | grep -E '^(DATABASE_URL|JWT_SECRET)=') \\
        -e YOLO_CONFIG_DIR=/work/.ultralytics -e HOME=/work \\
        -v "$PWD/backend:/app" -v "$PWD/data:/app/data:ro" -v "$PWD/weights:/app/weights:ro" \\
        -v <workdir>:/work -w /work \\
        --entrypoint python maptimize-maptimize-backend /app/scripts/train_detector.py build --out /work/ds
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from sqlalchemy import select  # noqa: E402

from database import get_db_context  # noqa: E402
from ml.detection.detector import CellDetector, model_input, normalize_image  # noqa: E402
from ml.detection.training import (  # noqa: E402
    HUMAN,
    NEGATIVE,
    REVIEWED,
    UNREVIEWED,
    Evaluation,
    FovResult,
    crop_box,
    paired_bootstrap,
    provenance,
    split_experiments,
    yolo_label,
)
from models.cell_crop import CellCrop  # noqa: E402
from models.experiment import Experiment  # noqa: E402
from models.image import Image  # noqa: E402
from services.image_processor import ImageProcessor  # noqa: E402

SPLIT_SEED = 0
CONF_GRID = [round(c, 2) for c in np.arange(0.05, 0.96, 0.05)]

# Microscopy has no colour and no up: hue/saturation off, both flips.
#
# ⚠️ NO rotation and NO shear, although a cell has no orientation. The January
# model was trained with degrees=180, shear=10, and that is why its boxes were
# loose: ultralytics turns a box under an affine warp into the min/max envelope
# of its four warped corners (RandomPerspective.apply_bboxes), so at a uniform
# angle the label is on average 4/pi = 27 % larger than the cell. The network
# learns the inflation and reads how much to apply from the grey corners the
# rotation leaves. Measured 2026-11-08, same data, same architecture:
#   degrees=180: box area 1.09-1.19x the annotated area; test AP50-95
#                0.478 (YOLO11s) / 0.544 (YOLO26s)
#   degrees=0:   box area 1.03-1.08x; test AP50-95 0.545 / 0.559, and on val
#                +0.068 / +0.032 with bootstrap CIs above zero. Detection F1
#                moved -0.013 / +0.003 on val, CIs straddling zero.
# ultralytics' own `val` hid it: its rect batches add a grey border
# (pad=0.5 stride), the context the network associates with rotation, so it
# scored 0.53 on boxes production would draw at 0.36. Flips are the only
# box-preserving symmetries ultralytics offers, so they stay.
AUGMENT = dict(
    hsv_h=0.0, hsv_s=0.0, hsv_v=0.3,
    degrees=0.0, translate=0.15, scale=0.3, shear=0.0,
    flipud=0.5, fliplr=0.5, mosaic=0.5, mixup=0.0, close_mosaic=20,
)


def source_path(image: Image) -> str:
    """The file production detection reads (``ImageProcessor.process_batch``)."""
    if image.mip_path and Path(image.mip_path).exists():
        return image.mip_path
    return image.file_path


async def load_detector_view(image: Image) -> np.ndarray:
    """``normalize_image`` of the source, exactly as ``_run_detection`` builds it."""
    raw = await ImageProcessor(image.id)._load_image(source_path(image))
    if raw is None:
        raise ValueError(f"cannot load image {image.id}")
    return normalize_image(raw)


async def load_fovs():
    """Every FOV with its experiment, owner and crops."""
    async with get_db_context() as db:
        images = (
            await db.execute(
                select(Image, Experiment.user_id)
                .join(Experiment, Image.experiment_id == Experiment.id)
                .order_by(Image.id)
            )
        ).all()
        crops = (
            await db.execute(
                select(
                    CellCrop.image_id, CellCrop.bbox_x, CellCrop.bbox_y, CellCrop.bbox_w,
                    CellCrop.bbox_h, CellCrop.bbox_angle, CellCrop.detection_confidence,
                )
            )
        ).all()
    by_image: dict[int, list] = {}
    for c in crops:
        by_image.setdefault(c.image_id, []).append(c)
    return [(img, owner, by_image.get(img.id, [])) for img, owner in images]


async def build(out: Path) -> None:
    import cv2

    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    detector = CellDetector(device="cuda")  # production weights and thresholds

    fovs, skipped = [], Counter()
    t0 = time.monotonic()
    for k, (image, owner, crops) in enumerate(await load_fovs()):
        if not image.width or not image.height:
            skipped["no dimensions"] += 1
            continue
        try:
            view = await load_detector_view(image)
        except Exception as exc:  # a missing file must not abort a 1800-image build
            skipped[f"unreadable: {type(exc).__name__}"] += 1
            continue
        h, w = view.shape[:2]
        if (w, h) != (image.width, image.height):
            skipped["size differs from DB"] += 1
            continue

        boxes = []
        for c in crops:
            box = crop_box(c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h, c.bbox_angle, w, h)
            if box is not None:
                boxes.append((box, c.detection_confidence))
        predicted = []
        if image.detect_cells:
            predicted = [
                (d.bbox_x, d.bbox_y, d.bbox_x + d.bbox_w, d.bbox_y + d.bbox_h)
                for d in detector.detect(view)
            ]
        kind = provenance(image.detect_cells, boxes, predicted)
        if kind is None:
            skipped["nothing to learn"] += 1
            continue

        cv2.imwrite(str(out / "images" / f"{image.id}.png"), model_input(view))
        (out / "labels" / f"{image.id}.txt").write_text(
            "".join(yolo_label(box, w, h) + "\n" for box, _ in boxes)
        )
        fovs.append({
            "image_id": image.id,
            "experiment_id": image.experiment_id,
            "owner": owner,
            "detect_cells": image.detect_cells,
            "provenance": kind,
            "width": w,
            "height": h,
            "gt": [list(box) for box, _ in boxes],
        })
        if k % 100 == 0:
            print(f"  {k} FOVs read, {time.monotonic() - t0:.0f}s", flush=True)

    # Only experiments with hand-checked FOVs can be held out: the others have
    # nothing to measure against.
    owners = {f["experiment_id"]: f["owner"] for f in fovs if f["provenance"] == HUMAN}
    split = split_experiments(owners, SPLIT_SEED)
    for f in fovs:
        f["split"] = split.get(f["experiment_id"], "train")

    def listing(name: str, keep) -> None:
        ids = [f["image_id"] for f in fovs if keep(f)]
        (out / f"{name}.txt").write_text("".join(f"./images/{i}.png\n" for i in ids))

    curated = {HUMAN, REVIEWED, NEGATIVE}
    listing("train_curated", lambda f: f["split"] == "train" and f["provenance"] in curated)
    listing("train_all", lambda f: f["split"] == "train")
    # Held-out experiments are scored only on boxes a person drew or kept.
    listing("val", lambda f: f["split"] == "val" and f["provenance"] == HUMAN)
    listing("test", lambda f: f["split"] == "test" and f["provenance"] == HUMAN)
    for pool in ("curated", "all"):
        (out / f"{pool}.yaml").write_text(
            f"path: {out}\ntrain: train_{pool}.txt\nval: val.txt\ntest: test.txt\nnames:\n  0: cell\n"
        )
    (out / "manifest.json").write_text(json.dumps({"seed": SPLIT_SEED, "fovs": fovs}, indent=1))

    table = Counter((f["split"], f["provenance"]) for f in fovs)
    print("\nFOVs by split x provenance (boxes):")
    for (s, p), n in sorted(table.items()):
        nb = sum(len(f["gt"]) for f in fovs if f["split"] == s and f["provenance"] == p)
        print(f"  {s:5s} {p:10s} {n:5d} FOVs {nb:5d} boxes")
    detected = [f for f in fovs if f["provenance"] in (REVIEWED, UNREVIEWED)]
    if detected:
        same = sum(f["provenance"] == UNREVIEWED for f in detected)
        print(f"\nall-detector FOVs reproduced exactly by today's model: {same}/{len(detected)}")
    print("skipped:", dict(skipped))


def train(data: Path, pool: str, init: str, imgsz: int, epochs: int, batch: int, name: str) -> None:
    from ultralytics import YOLO

    YOLO(init).train(
        data=str(data / f"{pool}.yaml"),
        imgsz=imgsz,
        epochs=epochs,
        batch=batch,
        patience=30,
        device=0,
        # The server has 4 cores shared with production: decode each PNG once
        # (resized to imgsz, ~1.3 GB at 640) and leave one core free.
        cache="ram",
        workers=3,
        seed=0,
        deterministic=True,
        project=str(data / "runs"),
        name=name,
        exist_ok=True,
        plots=False,
        single_cls=True,
        **AUGMENT,
    )


async def predict(data: Path, split: str, name: str, weights: str) -> list[FovResult]:
    """Run one model over one split through the production call; cached."""
    cache = data / "preds" / f"{name}_{split}.json"
    manifest = [f for f in json.loads((data / "manifest.json").read_text())["fovs"]
                if f["split"] == split and f["provenance"] == HUMAN]
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        detector = CellDetector(weights_path=Path(weights), device="cuda")
        async with get_db_context() as db:
            images = {
                i.id: i for i in (await db.execute(
                    select(Image).where(Image.id.in_([f["image_id"] for f in manifest]))
                )).scalars()
            }
        raw = {}
        for f in manifest:
            view = await load_detector_view(images[f["image_id"]])
            dets = detector.detect(view, conf=0.001)
            raw[str(f["image_id"])] = [
                [d.bbox_x, d.bbox_y, d.bbox_x + d.bbox_w, d.bbox_y + d.bbox_h, d.confidence]
                for d in dets
            ]
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(raw))
    # JSON keys are strings, so the fresh dict uses them too.
    return [
        FovResult(
            image_id=f["image_id"],
            experiment_id=f["experiment_id"],
            pred_boxes=[tuple(p[:4]) for p in raw[str(f["image_id"])]],
            pred_scores=[p[4] for p in raw[str(f["image_id"])]],
            gt_boxes=[tuple(b) for b in f["gt"]],
        )
        for f in manifest
    ]


def summarise(ev: Evaluation, idx: list[int], conf: float) -> dict:
    return {
        "fovs": len(idx),
        "boxes": sum(len(ev.fovs[i].gt_boxes) for i in idx),
        "ap50": ev.ap(idx, 0.5),
        "ap50_95": ev.ap50_95(idx),
        "at_0.5": ev.operating_point(idx, 0.5),
        f"at_{conf}": ev.operating_point(idx, conf),
    }


async def evaluate(data: Path, models: list[tuple[str, str]], out: Path) -> None:
    manifest = {f["image_id"]: f for f in json.loads((data / "manifest.json").read_text())["fovs"]}
    report = {"models": {}, "vs_baseline": {}}
    test_evals = {}
    for name, weights in models:
        val = Evaluation(await predict(data, "val", name, weights))
        test = Evaluation(await predict(data, "test", name, weights))
        conf = test.conf = val.best_conf(range(len(val.fovs)), CONF_GRID)
        everything = list(range(len(test.fovs)))
        # Blind: detection was off, the annotator never saw a prediction.
        # Corrected: they fixed the old model's output — chosen where it failed.
        blind = [i for i, f in enumerate(test.fovs) if not manifest[f.image_id]["detect_cells"]]
        corrected = [i for i, f in enumerate(test.fovs) if manifest[f.image_id]["detect_cells"]]
        report["models"][name] = {
            "weights": weights,
            "conf_chosen_on_val": conf,
            "val": summarise(val, list(range(len(val.fovs))), conf),
            "test": summarise(test, everything, conf),
            "test_blind": summarise(test, blind, conf),
            "test_corrected": summarise(test, corrected, conf),
        }
        test_evals[name] = test

    base = test_evals[models[0][0]]
    for name, ev in list(test_evals.items())[1:]:
        report["vs_baseline"][name] = {
            "ap50": paired_bootstrap(base, ev, lambda e, i: e.ap(i, 0.5)),
            "ap50_95": paired_bootstrap(base, ev, lambda e, i: e.ap50_95(i)),
            # Each model at its own val-chosen threshold: what production would run.
            "f1": paired_bootstrap(base, ev, lambda e, i: e.operating_point(i, e.conf)["f1"]),
        }
    out.write_text(json.dumps(report, indent=1, default=float))
    print(json.dumps(report, indent=1, default=float))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, required=True)
    t = sub.add_parser("train")
    t.add_argument("--data", type=Path, required=True)
    t.add_argument("--pool", choices=["curated", "all"], default="curated")
    t.add_argument("--init", required=True, help="yolov8s.pt, yolo11s.pt, or a checkpoint to fine-tune")
    t.add_argument("--imgsz", type=int, default=640)
    t.add_argument("--epochs", type=int, default=150)
    t.add_argument("--batch", type=int, default=16)
    t.add_argument("--name", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--data", type=Path, required=True)
    e.add_argument("--weights", nargs="+", required=True, help="name=path; the first is the baseline")
    e.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.command != "train":
        # Image paths in the DB are relative to the backend's working directory.
        # ``train`` stays in the caller's, so ultralytics downloads land there.
        args_paths = [getattr(args, a) for a in ("out", "data") if getattr(args, a, None)]
        if any(not p.is_absolute() for p in args_paths):
            parser.error("--out / --data must be absolute paths")
        os.chdir(APP_ROOT)
    if args.command == "build":
        asyncio.run(build(args.out))
    elif args.command == "train":
        train(args.data, args.pool, args.init, args.imgsz, args.epochs, args.batch, args.name)
    else:
        models = [tuple(w.split("=", 1)) for w in args.weights]
        asyncio.run(evaluate(args.data, models, args.out or args.data / "report.json"))


if __name__ == "__main__":
    main()
