import en from "../../messages/en.json";

/**
 * Render to markup without React.
 *
 * Playwright compiles JSX in every file it loads — this one and the component
 * — to its own `{ type, props }` records (the component-testing runtime), which
 * react-dom refuses. These components are hook-free functions of their props,
 * so calling them and walking the records IS a faithful render.
 */
type Jsx = { type: string | symbol | ((props: object) => unknown); props: Record<string, unknown> };

export function renderToStaticMarkup(node: unknown): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(renderToStaticMarkup).join("");
  const { type, props } = node as Jsx;
  if (typeof type === "function") return renderToStaticMarkup(type(props));
  // A fragment (`<>…</>`) is a symbol, not a tag: it renders only its children.
  if (typeof type !== "string") return renderToStaticMarkup(props.children);
  const { children, ...attributes } = props;
  const rendered = Object.entries(attributes)
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([name, value]) => ` ${name === "className" ? "class" : name}="${String(value)}"`)
    .join("");
  return `<${type}${rendered}>${renderToStaticMarkup(children)}</${type}>`;
}

/** The real English strings, with `{placeholders}` filled the way next-intl would. */
export const t = (key: string, values: Record<string, string | number> = {}): string => {
  const template = (en.umap as Record<string, string>)[key];
  if (template === undefined) throw new Error(`missing umap.${key}`);
  return template.replace(/\{(\w+)\}/g, (_, name) => {
    if (!(name in values)) throw new Error(`umap.${key}: no value for {${name}}`);
    return String(values[name]);
  });
};

/** Text content of the rendered markup. */
export const text = (html: string): string =>
  html.replace(/<[^>]+>/g, "");
