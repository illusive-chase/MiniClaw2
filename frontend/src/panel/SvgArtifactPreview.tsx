export function SvgArtifactPreview({ name, rawUrl }: { name: string; rawUrl: string }) {
  return (
    <div className="overflow-hidden rounded-md border border-line bg-white shadow-card">
      <img
        alt={name}
        src={rawUrl}
        referrerPolicy="no-referrer"
        className="h-[min(65vh,560px)] w-full object-contain"
      />
    </div>
  );
}
