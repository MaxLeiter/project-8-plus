/** The Project 8 Plus wordmark: condensed caps + the pink PLUS badge (boot screen, launcher, trailer cards). */
export function Lockup({ className, as: Tag = "span" }: { className?: string; as?: "span" | "h1" | "div" }) {
  return (
    <Tag className={["lockup", className].filter(Boolean).join(" ")}>
      <span>Project 8</span> <span className="plus-pill">Plus</span>
    </Tag>
  );
}
