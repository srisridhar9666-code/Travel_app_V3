/** Where a trip goes, as a person would say it. Mirrors `route_label` on the
 *  server, so the screen and the emails read the same. */

interface Routed {
  origin: string | null;
  destination: string | null;
  pickup_city?: string | null;
  drop_city?: string | null;
}

/** "Banjara Hills, Hyderabad" - a cab's address with the city it is in,
 *  unless the address already names it. */
function withCity(place: string | null | undefined, city: string | null | undefined): string {
  const address = (place ?? '').trim();
  const town = (city ?? '').trim();
  if (!town || address.toLowerCase().includes(town.toLowerCase())) return address || town;
  return address ? `${address}, ${town}` : town;
}

export function routeLabel(trip: Routed, separator = ' → '): string {
  return `${withCity(trip.origin, trip.pickup_city)}${separator}${withCity(
    trip.destination,
    trip.drop_city,
  )}`;
}

export function routeParts(trip: Routed): { origin: string; destination: string } {
  return {
    origin: withCity(trip.origin, trip.pickup_city) || '—',
    destination: withCity(trip.destination, trip.drop_city) || '—',
  };
}

