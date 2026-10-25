import { fallbackSrc, image, srcSet, type ImageEntry } from "@/lib/media";

type Props = {
  id?: string;
  entry?: ImageEntry;
  alt: string;
  sizes?: string;
  priority?: boolean;
  className?: string;
  style?: React.CSSProperties;
};

/** <picture> with AVIF + WebP at every encoded width, intrinsic size (no layout shift) and a blurred placeholder. */
export function Picture({ id, entry, alt, sizes = "(max-width: 900px) 100vw, 60vw", priority, className, style }: Props) {
  const e = entry ?? image(id!);
  const types = [...new Set(e.files.map((f) => f.type))];
  const order = ["image/avif", "image/webp", "image/jpeg"].filter((t) => types.includes(t));
  return (
    <picture>
      {order.slice(0, -1).map((t) => (
        <source key={t} type={t} srcSet={srcSet(e, t)} sizes={sizes} />
      ))}
      <img
        src={fallbackSrc(e)}
        srcSet={srcSet(e, order[order.length - 1])}
        sizes={sizes}
        width={e.w}
        height={e.h}
        alt={alt}
        loading={priority ? "eager" : "lazy"}
        decoding={priority ? "sync" : "async"}
        fetchPriority={priority ? "high" : undefined}
        className={["ph", className].filter(Boolean).join(" ")}
        style={{ backgroundImage: `url(${e.placeholder})`, ...style }}
      />
    </picture>
  );
}
