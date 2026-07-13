import Skeleton from "../Skeleton.jsx";

// A placeholder shaped like the real boarding-pass TravelTicket, shown ONLY
// while the crew is generating the final itinerary (never for a follow-up
// question — that shows the light typing indicator instead). Paired with
// StagingIndicator's rotating status text, which carries the "what phase is it
// in" signal this shape can't.
export default function TicketSkeleton() {
  return (
    <div className="w-full max-w-2xl overflow-hidden rounded-3xl border border-line bg-surface shadow-[0_4px_28px_rgba(30,30,60,0.08)]">
      {/* Header band */}
      <div className="flex items-center justify-between bg-gradient-to-br from-brand to-brand-strong px-5 py-3.5">
        <Skeleton className="h-4 w-40 bg-white/30" />
        <Skeleton className="h-4 w-24 rounded-full bg-white/30" />
      </div>

      <div className="px-5 pt-5 pb-4">
        <Skeleton className="h-2.5 w-24" />
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="space-y-2">
            <Skeleton className="h-8 w-16" />
            <Skeleton className="h-3 w-12" />
          </div>
          <Skeleton className="h-4 flex-1" />
          <div className="space-y-2">
            <Skeleton className="h-8 w-16" />
            <Skeleton className="h-3 w-12" />
          </div>
        </div>
        <div className="mt-5 grid grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="space-y-1.5">
              <Skeleton className="h-2.5 w-12" />
              <Skeleton className="h-3.5 w-16" />
            </div>
          ))}
        </div>
      </div>

      <div className="px-5">
        <div className="ticket-notch" />
      </div>

      <div className="flex items-center justify-between gap-4 px-5 pt-5 pb-4">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-8 flex-1" />
        <Skeleton className="h-3 w-16" />
      </div>

      <div className="space-y-3 border-t border-line px-5 py-5">
        <Skeleton className="h-3 w-24" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="space-y-2 rounded-2xl border border-line p-3.5">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        ))}
      </div>
    </div>
  );
}
