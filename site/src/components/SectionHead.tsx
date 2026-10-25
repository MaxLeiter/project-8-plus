/** A big condensed section title and an optional lede beside it. */
export function SectionHead({ title, lede, id }: { title: React.ReactNode; lede?: React.ReactNode; id: string }) {
  return (
    <header className="section-head">
      <h2 className="section-title" id={id}>
        {title}
      </h2>
      {lede ? <p className="lede">{lede}</p> : null}
    </header>
  );
}
