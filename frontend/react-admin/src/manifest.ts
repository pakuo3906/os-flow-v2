import type { ManifestResponse } from "./types";
import { apiUrl } from "./api";

export async function loadManifest(): Promise<ManifestResponse> {
  const response = await fetch(apiUrl("/admin/react-admin"));
  const payload = (await response.json()) as ManifestResponse;
  if (!response.ok) {
    throw new Error(`Failed to load manifest: ${JSON.stringify(payload)}`);
  }
  return payload;
}
