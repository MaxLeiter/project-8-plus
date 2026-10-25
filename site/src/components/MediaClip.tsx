import { Clip } from "./Clip";
import { clipData } from "@/lib/media";

/** Server wrapper: looks the clip up in the media manifest and renders the client player. */
export function MediaClip({ id, label, className }: { id: string; label: string; className?: string }) {
  return <Clip clip={clipData(id)} label={label} className={className} />;
}
