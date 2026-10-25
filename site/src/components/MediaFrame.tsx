import { Maximize2 } from "lucide-react";
import type { LightboxItem } from "@/lib/media";

/** A framed image or clip that opens in the lightbox (click the media, or the button for keyboard users). */
export function MediaFrame({ item, children, className }: { item: LightboxItem; children: React.ReactNode; className?: string }) {
  return (
    <div className={["frame", "lb-frame", className].filter(Boolean).join(" ")} data-lb={JSON.stringify(item)}>
      {children}
      <button type="button" className="lb-btn" data-lb-open aria-label={`View larger: ${item.alt}`} title="View larger">
        <Maximize2 size={15} aria-hidden />
      </button>
    </div>
  );
}
