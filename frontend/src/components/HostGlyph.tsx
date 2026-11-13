import { hostGlyph, hostTextClass } from "../hostIcons";

/** A host's icon stroked in its color; null keys draw the fallback in ink. */
export function HostGlyph({
  icon,
  color,
  size = 14,
  className = "",
}: {
  icon: string | null | undefined;
  color: string | null | undefined;
  size?: number;
  className?: string;
}) {
  const Glyph = hostGlyph(icon).icon;
  return (
    <Glyph
      aria-hidden="true"
      size={size}
      strokeWidth={2}
      className={`flex-none ${hostTextClass(color)} ${className}`}
    />
  );
}
