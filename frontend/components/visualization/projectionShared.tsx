"use client";

/**
 * Pieces shared by every scatter projection on the dashboard.
 *
 * UMAP and the supervised discriminant differ only in where their coordinates
 * come from and what has to be said about them; the tooltips, the legend and
 * the point styling are the same work. They live here so the two views cannot
 * drift into disagreeing about what a point means.
 */
import {
  api,
  API_URL,
  type DiscriminantPoint,
  type UmapFovPoint,
  type UmapPoint,
} from "@/lib/api";
import { MicroscopyImage } from "@/components/ui";
import type { TooltipProps } from "recharts";
import {
  MARKER_KINDS,
  MIN_DOT_RADIUS,
  markerStyle,
  pointRadius,
  type PtmKind,
} from "./pointMarker";

/** Any point one of the projections can draw. */
export type ProjectionPoint = UmapPoint | UmapFovPoint | DiscriminantPoint;

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
}

/** Resolves a point to the experiment metadata the facet summary carries. */
export type ContextResolver = (point: ProjectionPoint) => PointContext;

/** The rows shared by both tooltips: where this point came from. */
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
    </>
  );
}

// Tooltip for cropped cell view. Serves the discriminant projection too: its
// points carry the same crop identity, so there is nothing extra to say.
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
        {point.bundleness_score !== null && (
          <div className="text-xs text-text-secondary">
            {t("bundleness")}: {point.bundleness_score.toFixed(2)}
          </div>
        )}
        <div className="text-xs text-text-muted">{t("cropId", { id: point.crop_id })}</div>
      </div>
    </div>
  );
}

// Tooltip for FOV view
interface FovTooltipProps extends TooltipProps<number, string> {
  t: Translate;
  contextOf: ContextResolver;
}

export function FovTooltip({
  active,
  payload,
  t,
  contextOf,
}: FovTooltipProps): JSX.Element | null {
  if (!active || !payload || !payload.length) return null;

  const point = payload[0].payload as UmapFovPoint;

  return (
    <div className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl p-3 max-w-[250px]">
      <MicroscopyImage
        src={buildAuthenticatedUrl(point.thumbnail_url)}
        alt="FOV thumbnail"
        className="w-full h-40 object-contain rounded mb-2 bg-black/50"
        onError={hideOnError}
      />
      <div className="space-y-1">
        <div className="font-medium text-text-primary truncate text-sm">
          {point.original_filename}
        </div>
        <div
          className="text-sm flex items-center gap-2"
          style={{ color: point.protein_color }}
        >
          <span
            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
            style={{ backgroundColor: point.protein_color }}
          />
          {point.protein_name || t("unassigned")}
        </div>
        <ContextRows context={contextOf(point)} t={t} />
        <div className="text-xs text-text-muted">{t("imageId", { id: point.image_id })}</div>
      </div>
    </div>
  );
}

/**
 * Swatches under the plot, one per group the points were coloured into.
 *
 * Fed from the same styleOf as the points themselves, so a swatch can never
 * disagree with what is drawn.
 */
export function ProjectionLegend({
  groups,
}: {
  groups: Array<{ name: string; color: string; count: number }>;
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
        </div>
      ))}
    </div>
  );
}

/**
 * What recharts hands a custom `shape`.
 *
 * The point object it builds has the matching `<Cell>`'s props merged in, so
 * `fill` is whatever `styleOf` decided — this component never picks a colour, it
 * only decides geometry. `size` is the ZAxis range value, and it is an AREA.
 */
export interface MarkerShapeProps {
  cx?: number;
  cy?: number;
  fill?: string;
  size?: number;
  payload?: ProjectionPoint;
  kind: PtmKind;
}

/**
 * One point, drawn with its sample kind.
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
  kind,
}: MarkerShapeProps): JSX.Element {
  // An empty group rather than null: recharts types a custom shape as returning
  // an Element, and a point with no coordinates has nothing to draw anyway.
  if (cx === undefined || cy === undefined) return <g />;

  const color = fill || DEFAULT_MARKER_COLOR;
  const style = markerStyle(kind, color);
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
      {style.dotRatio > 0 && (
        <circle
          cx={cx}
          cy={cy}
          r={Math.max(r * style.dotRatio, MIN_DOT_RADIUS)}
          fill={style.dotColor}
        />
      )}
    </g>
  );
}

// Only reached when a Cell somehow supplied no fill; matches DEFAULT_POINT_COLOR
// in chartConfig, kept local so this file does not import the chart config for
// one fallback.
const DEFAULT_MARKER_COLOR = "#888888";

/**
 * The key to the marker channel, drawn with the same component as the points.
 *
 * Hidden when every point on the plot is the same kind: a lab that has recorded
 * no PTM would otherwise be handed a legend explaining a distinction its plot
 * does not make.
 */
export function MarkerLegend({
  counts,
  t,
}: {
  counts: Map<PtmKind, number>;
  t: Translate;
}): JSX.Element | null {
  if (counts.size < 2) return null;

  const label: Record<PtmKind, string> = {
    none: t("markerNone"),
    modification: t("markerModification"),
    control: t("markerControl"),
  };

  return (
    <div className="flex flex-wrap items-center gap-3 mt-2">
      <span className="text-xs uppercase tracking-wide text-text-muted">
        {t("markerLegendTitle")}
      </span>
      {MARKER_KINDS.filter((kind) => counts.has(kind)).map((kind) => (
        <div key={kind} className="flex items-center gap-1.5">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            {/* Grey rather than a protein colour: the swatch is about the
                marker, and borrowing a hue would read as a fourth colour group
                in a legend that sits right below the colour one. */}
            <ProjectionMarker cx={8} cy={8} fill="#9ca3af" size={60} kind={kind} />
          </svg>
          <span className="text-xs text-text-secondary">
            {label[kind]} <span className="text-text-muted">({counts.get(kind)})</span>
          </span>
        </div>
      ))}
    </div>
  );
}
