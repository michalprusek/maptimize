"use client";

/**
 * Pieces of the dashboard's crop projection.
 *
 * Every point is a cell crop. The tooltip, the legend, the marker channel and
 * the acquisition-context rows live here, apart from the component that owns
 * the state, so what a point means and how it is drawn is decided in one place.
 */
import {
  api,
  API_URL,
  type BundlenessClass,
  type UmapPoint,
} from "@/lib/api";
import { MicroscopyImage } from "@/components/ui";
import type { TooltipProps } from "recharts";
import { ClassBundleness } from "./BundlenessBadge";
import { formatSigned, type LegendEntry } from "./bundleness";
import { DEFAULT_POINT_COLOR } from "./chartConfig";
import {
  MARKER_CLASSES,
  dotRadius,
  markerStyle,
  pointRadius,
  shouldShowMarkerLegend,
  type SampleClass,
} from "./pointMarker";

/** A point of the projection: one cell crop. */
export type ProjectionPoint = UmapPoint;

/** next-intl's `t`, narrowed to what these components use. */
export type Translate = (
  key: string,
  values?: Record<string, string | number>
) => string;

/** Build authenticated URL by appending token as query parameter */
export function buildAuthenticatedUrl(thumbnailUrl: string): string {
  const token = api.getToken();
  const separator = thumbnailUrl.includes("?") ? "&" : "?";
  return `${API_URL}${thumbnailUrl}${separator}token=${token}`;
}

/** Hide element on image load error */
export function hideOnError(e: React.SyntheticEvent<HTMLImageElement>): void {
  e.currentTarget.style.display = "none";
}

/** The acquisition context of a point, resolved from the facet summary. */
export interface PointContext {
  experimentName: string;
  microscopeName: string | null;
  ptmName: string | null;
  cellLineName: string | null;
}

/** Resolves a point to the experiment metadata the facet summary carries. */
export type ContextResolver = (point: ProjectionPoint) => PointContext;

/** Where a point came from. */
export function ContextRows({
  context,
  t,
}: {
  context: PointContext;
  t: Translate;
}): JSX.Element {
  return (
    <>
      <div className="text-xs text-text-secondary truncate">{context.experimentName}</div>
      {context.microscopeName && (
        <div className="text-xs text-text-muted truncate">
          {t("facetMicroscope")}: {context.microscopeName}
        </div>
      )}
      {context.ptmName && (
        <div className="text-xs text-text-muted truncate">
          {t("facetPtm")}: {context.ptmName}
        </div>
      )}
      {context.cellLineName && (
        <div className="text-xs text-text-muted truncate">
          {t("facetCellLine")}: {context.cellLineName}
        </div>
      )}
    </>
  );
}

// Leads with the protein, because a crop has no name of its own.
interface CroppedTooltipProps extends TooltipProps<number, string> {
  t: Translate;
  contextOf: ContextResolver;
}

export function CroppedTooltip({
  active,
  payload,
  t,
  contextOf,
}: CroppedTooltipProps): JSX.Element | null {
  if (!active || !payload || !payload.length) return null;

  const point = payload[0].payload as UmapPoint;
  // Signed like the badge, so a crop's value and the mean beside the plot read
  // on the same scale. Null for a crop the active model has not scored.
  const bundleness = formatSigned(point.bundleness_score);

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3 max-w-[220px]">
      <MicroscopyImage
        src={buildAuthenticatedUrl(point.thumbnail_url)}
        alt="Cell crop"
        className="w-full h-32 object-contain rounded mb-2 bg-black/50"
        onError={hideOnError}
      />
      <div className="space-y-1">
        <div
          className="font-medium text-text-primary flex items-center gap-2"
          style={{ color: point.protein_color }}
        >
          <span
            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
            style={{ backgroundColor: point.protein_color }}
          />
          {point.protein_name || t("unassigned")}
        </div>
        <ContextRows context={contextOf(point)} t={t} />
        {bundleness !== null && (
          <div className="text-xs text-text-secondary">
            {t("bundleness")}: <span className="font-mono">{bundleness}</span>
          </div>
        )}
        <div className="text-xs text-text-muted">{t("cropId", { id: point.crop_id })}</div>
      </div>
    </div>
  );
}

/**
 * Swatches under the plot, one per group the points were coloured into.
 *
 * Fed from the same styleOf as the points themselves, so a swatch can never
 * disagree with what is drawn.
 *
 * `means` holds each class' mean bundleness BY CLASS ID. The caller passes an
 * empty map whenever the response on screen does not break the mean down by the
 * axis these swatches show (see `classMeansById`), and an entry with no id —
 * "Unassigned", "in several groups" — has no class and so no number.
 */
export function ProjectionLegend({
  groups,
  means,
  meansStale,
  t,
}: {
  groups: LegendEntry[];
  means: Map<number, BundlenessClass>;
  /** The means describe the previous request; draw them dimmed. */
  meansStale: boolean;
  t: Translate;
}): JSX.Element {
  return (
    <div className="flex flex-wrap gap-3 mt-4 pt-4 border-t border-white/5">
      {groups.map((group) => (
        <div
          key={group.name}
          className="flex items-center gap-1.5 px-2 py-1 rounded bg-white/5"
        >
          <div
            className="w-3 h-3 rounded-full"
            style={{ backgroundColor: group.color }}
          />
          <span className="text-xs text-text-secondary">
            {group.name} <span className="text-text-muted">({group.count})</span>
          </span>
          <ClassBundleness
            cls={group.id === null ? undefined : means.get(group.id)}
            stale={meansStale}
            t={t}
          />
        </div>
      ))}
    </div>
  );
}

/**
 * What recharts actually hands a custom `shape`.
 *
 * Everything is optional because none of it is guaranteed — and `fill` is not
 * even in recharts' own `ScatterPointItem`: it arrives purely because
 * `Scatter.getComposedData` spreads the matching `<Cell>`'s props into the point
 * last. That is undocumented behaviour this component depends on for colour.
 */
export interface RechartsShapeProps {
  cx?: number;
  cy?: number;
  fill?: string;
  size?: number;
  payload?: ProjectionPoint;
}

/**
 * What `ProjectionMarker` needs: recharts' geometry plus the class the caller
 * must decide. Kept separate so a cast of recharts' props cannot assert `cls`,
 * and forgetting to pass it is a compile error rather than a plot that silently
 * reverts every point to the plain marker.
 */
export type MarkerShapeProps = RechartsShapeProps & { cls: SampleClass };

/**
 * One point, drawn with its sample class.
 *
 * Replaces recharts' default symbol rather than decorating it, because a centre
 * dot is a second element and a `<Cell>` can only set attributes on one. The
 * base circle is deliberately identical to what recharts drew before — same
 * radius from the same area, same fill opacity, same hairline stroke — so a plot
 * with no PTM recorded looks untouched.
 */
export function ProjectionMarker({
  cx,
  cy,
  fill,
  size = 60,
  cls,
}: MarkerShapeProps): JSX.Element {
  // The same three-way numeric check recharts' own `Symbols` does, and for the
  // same reason. ⚠️ `undefined` is NOT what a missing coordinate looks like:
  // `getCateCoordinateOfLine` returns **null**, React then drops the attribute,
  // and SVG defaults `cx` to 0 — so a nil coordinate would draw the point full
  // size, full colour, in the corner of the plot, indistinguishable from data.
  // A non-numeric `size` arrives the same way mid-animation.
  if (!Number.isFinite(cx) || !Number.isFinite(cy) || !Number.isFinite(size)) {
    return <g />;
  }

  const color = fill || DEFAULT_POINT_COLOR;
  const style = markerStyle(cls, color);
  const r = pointRadius(size);

  return (
    <g>
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill={color}
        fillOpacity={style.fillOpacity}
        stroke={style.stroke}
        strokeWidth={style.strokeWidth}
        cursor="pointer"
      />
      {style.dot && (
        <circle
          cx={cx}
          cy={cy}
          r={dotRadius(r, style.dot.ratio)}
          fill={style.dot.color}
        />
      )}
    </g>
  );
}

/**
 * The key to the marker channel, drawn with the same component as the points.
 *
 * Shown whenever anything on the plot is drawn differently from the default —
 * see `shouldShowMarkerLegend`, which is deliberately NOT "more than one class
 * present": filtering to a single PTM leaves every point wearing a black centre
 * dot with nothing to explain it, and filtering to controls leaves a plot of
 * faded rings, which reads as "de-emphasised" to anyone who was not told.
 */
export function MarkerLegend({
  counts,
  t,
}: {
  counts: Map<SampleClass, number>;
  t: Translate;
}): JSX.Element | null {
  if (!shouldShowMarkerLegend(counts)) return null;

  const label: Record<SampleClass, string> = {
    none: t("markerNone"),
    unrecorded: t("markerUnrecorded"),
    modification: t("markerModification"),
    control: t("markerControl"),
  };

  return (
    <div className="flex flex-wrap items-center gap-3 mt-2">
      <span className="text-xs uppercase tracking-wide text-text-muted">
        {t("markerLegendTitle")}
      </span>
      {MARKER_CLASSES.filter((cls) => counts.has(cls)).map((cls) => (
        <div key={cls} className="flex items-center gap-1.5">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            {/* Grey rather than a protein colour: the swatch is about the
                marker, and borrowing a hue would read as a fourth colour group
                in a legend that sits right below the colour one. */}
            <ProjectionMarker cx={8} cy={8} fill="#9ca3af" size={60} cls={cls} />
          </svg>
          <span className="text-xs text-text-secondary">
            {label[cls]} <span className="text-text-muted">({counts.get(cls)})</span>
          </span>
        </div>
      ))}
    </div>
  );
}
