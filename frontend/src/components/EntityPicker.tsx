import { useQuery } from "@tanstack/react-query";
import { Select, TextInput } from "./ui";
import { api } from "../lib/api";
import type { IssuerProfile } from "../lib/types";

/**
 * The legal-entity field every transport admin surface needs (WO-80).
 *
 * NO TRANSPORT ROUTE SERVES AN ENTITY NAME — the services key everything to an
 * `entity_id` and nothing else. The shared issuer registry (`GET
 * /issuer/registry`, gated on the DIFFERENT `ISSUED_READ` permission) holds the
 * same rows, so it is used as a CONVENIENCE exactly as `VatClaims.tsx` (WO-78)
 * uses it: `retry: false`, and when the caller cannot read it the field
 * degrades to a plain id input rather than blocking the screen. A VAT operator
 * without `ISSUED_READ` keeps every transport surface fully usable.
 */
export function useEntities(enabled: boolean) {
  return useQuery<IssuerProfile[]>({
    queryKey: ["issuer", "registry"],
    queryFn: async () => (await api.get("/issuer/registry")).data,
    enabled,
    retry: false,
  });
}

/** The registry's display name for an id, falling back to the id itself — which
 * is what an operator without registry access legitimately sees. */
export function entityLabel(entities: IssuerProfile[] | undefined, id: string): string {
  const found = entities?.find((e) => e.id === id);
  return found?.name || found?.legal_name || id;
}

export function EntityPicker({
  entities,
  value,
  onChange,
  label = "Legal entity",
  required,
}: {
  entities: IssuerProfile[] | undefined;
  value: string;
  onChange: (id: string) => void;
  label?: string;
  required?: boolean;
}) {
  if (entities && entities.length > 0) {
    return (
      <Select label={label} required={required} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {entities.map((e) => (
          <option key={e.id} value={e.id}>
            {e.name || e.legal_name || e.id}
          </option>
        ))}
      </Select>
    );
  }
  return (
    <TextInput
      label={label}
      required={required}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Entity id"
      hint="The registry isn’t readable here — paste the entity id."
    />
  );
}
