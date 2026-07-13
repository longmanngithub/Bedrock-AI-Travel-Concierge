import { PlaneIcon } from "../icons.jsx";

// ---- small deterministic helpers (stable across re-renders, no Date.now) ----
function airportCode(name) {
  const letters = (name || "").replace(/[^a-zA-Z]/g, "").toUpperCase();
  if (letters.length >= 3) return letters.slice(0, 3);
  return (letters + "XXX").slice(0, 3);
}

function hashString(s) {
  let h = 0;
  for (let i = 0; i < (s || "").length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

function bookingRef(itinerary) {
  const h = hashString(`${itinerary.destination}:${itinerary.num_days}`);
  return h.toString(36).toUpperCase().padStart(6, "0").slice(0, 6);
}

function flightNo(itinerary) {
  return `BR ${(hashString(itinerary.destination) % 900) + 100}`;
}

function formatMoney(value, currency) {
  if (value == null) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "USD",
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${Math.round(value).toLocaleString()} ${currency || ""}`.trim();
  }
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

// A CSS barcode: deterministic bar widths seeded from the booking ref, so every
// ticket has its own stable pattern.
function Barcode({ seed, className = "" }) {
  const h = hashString(seed);
  const bars = Array.from({ length: 42 }, (_, i) => {
    const bit = (h >> (i % 30)) & 1;
    const wide = ((h >> (i % 17)) & 3) === 0;
    return { w: wide ? 3 : bit ? 2 : 1, on: i % 5 !== 4 };
  });
  return (
    <div className={`flex items-end gap-[2px] ${className}`} aria-hidden="true">
      {bars.map((b, i) => (
        <span
          key={i}
          className={b.on ? "bg-ink" : "bg-transparent"}
          style={{ width: `${b.w}px`, height: "100%" }}
        />
      ))}
    </div>
  );
}

function Field({ label, value, className = "" }) {
  return (
    <div className={className}>
      <p className="text-[10px] font-medium tracking-wider text-muted uppercase">{label}</p>
      <p className="mt-0.5 truncate text-sm font-semibold text-ink">{value}</p>
    </div>
  );
}

function Section({ icon, label, children }) {
  return (
    <section className="space-y-2.5">
      <h4 className="flex items-center gap-2 text-xs font-semibold tracking-wide text-muted uppercase">
        {icon && <span aria-hidden="true">{icon}</span>}
        {label}
      </h4>
      {children}
    </section>
  );
}

function BudgetRow({ label, value, strong }) {
  return (
    <div
      className={`flex items-center justify-between py-1.5 ${
        strong ? "font-semibold text-ink" : "text-ink-soft"
      }`}
    >
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

// The personalized "boarding pass" presentation of a generated itinerary: an
// airline-style pass header (route, flight info, stub + barcode), then the full
// day-by-day itinerary body beneath it.
export default function TravelTicket({ itinerary, tripRequest, elapsedSeconds }) {
  if (!itinerary) return null;
  const budget = itinerary.budget || {};
  const currency = budget.currency || tripRequest?.currency || "USD";
  const days = itinerary.daily_plans || [];
  const originName = tripRequest?.origin || "Home";
  const destName = itinerary.destination;
  const travelers = tripRequest?.travelers || 1;
  const pace = tripRequest?.pace || "balanced";
  const ref = bookingRef(itinerary);
  const withinBudget = itinerary.within_budget;

  return (
    <div className="animate-msg-in w-full max-w-2xl overflow-hidden rounded-3xl border border-line bg-surface shadow-[0_4px_28px_rgba(30,30,60,0.08)]">
      {/* ---- Boarding-pass header ---- */}
      <div className="bg-gradient-to-br from-brand to-brand-strong px-5 py-3.5 text-on-brand">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <PlaneIcon className="h-4 w-4" />
            <span className="text-sm font-bold tracking-wide">BEDROCK AIRWAYS</span>
          </div>
          <span className="rounded-full bg-white/20 px-2.5 py-0.5 text-[10px] font-semibold tracking-wider uppercase">
            Concierge Class
          </span>
        </div>
      </div>

      <div className="px-5 pt-5 pb-4">
        <p className="text-[10px] font-medium tracking-[0.2em] text-muted uppercase">Boarding Pass</p>

        {/* Route */}
        <div className="mt-2 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-3xl font-extrabold tracking-tight text-ink">{airportCode(originName)}</p>
            <p className="mt-0.5 truncate text-xs text-muted">{originName}</p>
          </div>
          <div className="flex flex-1 items-center gap-2 text-brand">
            <span className="h-px flex-1 bg-gradient-to-r from-transparent to-brand/40" />
            <PlaneIcon className="h-5 w-5 rotate-90" />
            <span className="h-px flex-1 bg-gradient-to-l from-transparent to-brand/40" />
          </div>
          <div className="min-w-0 text-right">
            <p className="text-3xl font-extrabold tracking-tight text-ink">{airportCode(destName)}</p>
            <p className="mt-0.5 truncate text-xs text-muted">{destName}</p>
          </div>
        </div>

        {itinerary.summary && (
          <p className="mt-3 text-sm leading-relaxed text-ink-soft">{itinerary.summary}</p>
        )}

        {/* Flight info grid */}
        <div className="mt-4 grid grid-cols-3 gap-y-4 gap-x-3 sm:grid-cols-4">
          <Field label="Passenger" value={`${travelers} ${travelers === 1 ? "traveler" : "travelers"}`} />
          <Field label="Depart" value={formatDate(tripRequest?.start_date)} />
          <Field label="Return" value={formatDate(tripRequest?.end_date)} />
          <Field label="Duration" value={`${itinerary.num_days} ${itinerary.num_days === 1 ? "day" : "days"}`} />
          <Field label="Flight" value={flightNo(itinerary)} />
          <Field label="Pace" value={pace.charAt(0).toUpperCase() + pace.slice(1)} />
          <Field label="Budget" value={formatMoney(budget.total_estimated ?? tripRequest?.budget, currency)} />
          <Field label="Booking" value={ref} />
        </div>
      </div>

      {/* Perforated tear line */}
      <div className="relative px-5">
        <div className="ticket-notch" />
      </div>

      {/* ---- Stub: status + barcode ---- */}
      <div className="flex items-center justify-between gap-4 px-5 pt-5 pb-4">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${withinBudget ? "bg-ok" : "bg-warn"}`} />
          <span className="text-sm font-medium text-ink">
            {withinBudget ? "Within budget" : "Over budget"}
          </span>
        </div>
        <Barcode seed={ref} className="h-9 flex-1 justify-end" />
        <span className="font-mono text-xs tracking-widest text-muted">{ref}</span>
      </div>

      {/* ---- Itinerary body ---- */}
      <div className="space-y-6 border-t border-line px-5 py-5">
        {days.length > 0 && (
          <Section label="Day by day">
            <div className="space-y-3">
              {days.map((d) => (
                <div key={d.day} className="rounded-2xl border border-line bg-surface-2 p-3.5">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-ink">
                      <span className="mr-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-brand-soft px-1.5 text-xs font-bold text-brand">
                        {d.day}
                      </span>
                      {d.title || `Day ${d.day}`}
                    </p>
                    {!!d.estimated_cost && (
                      <span className="shrink-0 text-xs font-medium text-muted">
                        {formatMoney(d.estimated_cost, currency)}
                      </span>
                    )}
                  </div>
                  <div className="mt-2 space-y-1 text-sm text-ink-soft">
                    {d.morning && (
                      <p>
                        <span className="font-medium text-ink">Morning · </span>
                        {d.morning}
                      </p>
                    )}
                    {d.afternoon && (
                      <p>
                        <span className="font-medium text-ink">Afternoon · </span>
                        {d.afternoon}
                      </p>
                    )}
                    {d.evening && (
                      <p>
                        <span className="font-medium text-ink">Evening · </span>
                        {d.evening}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </Section>
        )}

        <Section label="Budget breakdown">
          <div className="rounded-2xl border border-line bg-surface-2 px-4 py-2 text-sm">
            <div className="divide-y divide-line">
              <BudgetRow label="Accommodation" value={formatMoney(budget.accommodation, currency)} />
              <BudgetRow label="Food" value={formatMoney(budget.food, currency)} />
              <BudgetRow label="Activities" value={formatMoney(budget.activities, currency)} />
              <BudgetRow label="Transport" value={formatMoney(budget.transport, currency)} />
              <BudgetRow label="Misc" value={formatMoney(budget.misc, currency)} />
              <BudgetRow label="Total" value={formatMoney(budget.total_estimated, currency)} strong />
            </div>
          </div>
          {budget.notes && <p className="text-xs text-muted">{budget.notes}</p>}
        </Section>

        {itinerary.attractions?.length > 0 && (
          <Section label="Attractions">
            <div className="flex flex-wrap gap-2">
              {itinerary.attractions.map((a, i) => (
                <span key={i} className="rounded-full bg-surface-2 px-3 py-1.5 text-sm text-ink-soft">
                  {a}
                </span>
              ))}
            </div>
          </Section>
        )}

        {itinerary.restaurants?.length > 0 && (
          <Section label="Where to eat">
            <ul className="space-y-2 text-sm">
              {itinerary.restaurants.map((r, i) => (
                <li key={i} className="flex items-baseline justify-between gap-3">
                  <span>
                    <span className="font-medium text-ink">{r.name}</span>
                    {r.cuisine ? <span className="text-muted"> · {r.cuisine}</span> : null}
                    {r.note ? <span className="text-ink-soft"> — {r.note}</span> : null}
                  </span>
                  {r.price_range && <span className="shrink-0 text-muted">{r.price_range}</span>}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {itinerary.accommodation_options?.length > 0 && (
          <Section label="Where to stay">
            <ul className="space-y-2 text-sm">
              {itinerary.accommodation_options.map((a, i) => (
                <li key={i} className="flex items-baseline justify-between gap-3">
                  <span>
                    <span className="font-medium text-ink">{a.name}</span>
                    {a.area ? <span className="text-muted"> · {a.area}</span> : null}
                    {a.note ? <span className="text-ink-soft"> — {a.note}</span> : null}
                  </span>
                  {a.price_range && <span className="shrink-0 text-muted">{a.price_range}</span>}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {(itinerary.personalization_notes ||
          itinerary.prioritize?.length > 0 ||
          itinerary.avoid?.length > 0) && (
          <Section label="Traveler profile">
            {itinerary.personalization_notes && (
              <p className="text-sm text-ink-soft">{itinerary.personalization_notes}</p>
            )}
            {itinerary.prioritize?.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {itinerary.prioritize.map((p, i) => (
                  <span
                    key={i}
                    className="rounded-full border border-ok/30 bg-ok/10 px-3 py-1 text-sm text-ink-soft"
                  >
                    {p}
                  </span>
                ))}
              </div>
            )}
            {itinerary.avoid?.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {itinerary.avoid.map((a, i) => (
                  <span
                    key={i}
                    className="rounded-full border border-line px-3 py-1 text-sm text-muted line-through"
                  >
                    {a}
                  </span>
                ))}
              </div>
            )}
          </Section>
        )}

        {itinerary.transportation?.length > 0 && (
          <Section label="Getting around">
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink-soft marker:text-muted">
              {itinerary.transportation.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </Section>
        )}

        {itinerary.traveler_notes?.length > 0 && (
          <Section label="Good to know">
            <ul className="space-y-1.5 text-sm text-ink-soft">
              {itinerary.traveler_notes.map((n, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-brand">•</span>
                  <span>{n}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        {itinerary.reviewer_notes && (
          <Section label="Concierge notes">
            <p className="rounded-2xl border border-brand/20 bg-brand-soft/50 p-3.5 text-sm text-ink-soft">
              {itinerary.reviewer_notes}
            </p>
          </Section>
        )}
      </div>

      <div className="border-t border-line px-5 py-3 text-xs text-muted">
        <p>
          Crafted by Bedrock's travel crew{elapsedSeconds ? ` · ${elapsedSeconds.toFixed(1)}s` : ""}
        </p>
        <p className="mt-1">
          AI-generated — prices, hours, and availability are estimates. Please
          verify details directly with venues before booking.
        </p>
      </div>
    </div>
  );
}
