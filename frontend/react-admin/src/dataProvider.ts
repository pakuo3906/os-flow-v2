import type { DataProvider, GetListParams, Identifier, RaRecord } from "react-admin";
import { apiUrl } from "./api";
import type { ManifestResource } from "./types";

type AnyRecord = Record<string, unknown> & { id: Identifier };

function buildQuery(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  return search.toString();
}

function pathTemplate(resource: ManifestResource, kind: "list" | "show" | "edit"): string {
  if (kind === "list") {
    return resource.listPath || "";
  }
  if (kind === "edit") {
    return resource.editPath || resource.showPath || "";
  }
  return resource.showPath || resource.detailPath || "";
}

function fillPath(template: string, id: Identifier): string {
  return template.replace(/\{[^}]+\}/g, encodeURIComponent(String(id)));
}

async function parseResponse(response: Response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(typeof payload?.detail === "string" ? payload.detail : JSON.stringify(payload));
  }
  return payload;
}

function toTotalCount(resource: ManifestResource, response: Response, payload: unknown): number {
  if (Array.isArray(payload)) {
    const header = response.headers.get("X-Total-Count");
    return header ? Number(header) : payload.length;
  }
  if (payload && typeof payload === "object") {
    const body = payload as { total?: number; items?: unknown[] };
    if (typeof body.total === "number") {
      return body.total;
    }
    if (Array.isArray(body.items)) {
      return body.items.length;
    }
  }
  return 0;
}

function normalizeList(resource: ManifestResource, payload: unknown): AnyRecord[] {
  if (Array.isArray(payload)) {
    return payload as AnyRecord[];
  }
  if (payload && typeof payload === "object") {
    const body = payload as { items?: AnyRecord[] };
    if (Array.isArray(body.items)) {
      return body.items;
    }
  }
  return [];
}

function listPathFor(resource: ManifestResource): string {
  return resource.listPath || "";
}

function readFilterValue(filter: Record<string, unknown>, key: string): string | number | undefined {
  const value = filter[key];
  if (typeof value === "string" || typeof value === "number") {
    return value;
  }
  return undefined;
}

export function createManifestDataProvider(resources: ManifestResource[]): DataProvider {
  const resourceMap = new Map(resources.map((resource) => [resource.name, resource]));

  const provider: DataProvider = {
    async getList(resource, params) {
      const manifest = resourceMap.get(resource);
      if (!manifest) {
        throw new Error(`Unknown resource: ${resource}`);
      }
      const query = buildQuery({
        limit: params.pagination.perPage,
        offset: (params.pagination.page - 1) * params.pagination.perPage,
        ...Object.fromEntries(
          Object.entries(params.filter ?? {}).map(([key, value]) => [key, readFilterValue(params.filter ?? {}, key)]),
        ),
      });
      const response = await fetch(apiUrl(`${listPathFor(manifest)}${query ? `?${query}` : ""}`));
      const payload = await parseResponse(response);
      const data = normalizeList(manifest, payload);
      return {
        data,
        total: toTotalCount(manifest, response, payload),
      };
    },
    async getOne(resource, params) {
      const manifest = resourceMap.get(resource);
      if (!manifest) {
        throw new Error(`Unknown resource: ${resource}`);
      }
      const response = await fetch(apiUrl(fillPath(pathTemplate(manifest, "show"), params.id)));
      const payload = await parseResponse(response);
      return { data: payload as RaRecord };
    },
    async getMany(resource, params) {
      const data = await Promise.all(params.ids.map(async (id) => (await provider.getOne(resource, { id })).data));
      return { data };
    },
    async getManyReference(resource, params) {
      const target = params.target;
      const filter = { ...params.filter, [target]: params.id };
      return provider.getList(resource, {
        pagination: params.pagination,
        sort: params.sort,
        filter,
      } as GetListParams);
    },
    async update(resource, params) {
      const manifest = resourceMap.get(resource);
      if (!manifest) {
        throw new Error(`Unknown resource: ${resource}`);
      }
      const response = await fetch(apiUrl(fillPath(pathTemplate(manifest, "edit"), params.id)), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params.data),
      });
      const payload = await parseResponse(response);
      return { data: payload as RaRecord };
    },
    async updateMany(resource, params) {
      await Promise.all(params.ids.map(async (id) => provider.update(resource, { id, data: params.data, previousData: {} as RaRecord })));
      return { data: params.ids };
    },
    async create() {
      throw new Error("Create is not supported by the current manifest-driven app.");
    },
    async delete(resource, params) {
      const manifest = resourceMap.get(resource);
      if (!manifest) {
        throw new Error(`Unknown resource: ${resource}`);
      }
      const response = await fetch(apiUrl(fillPath(pathTemplate(manifest, "edit"), params.id)), {
        method: "DELETE",
      });
      const payload = await parseResponse(response);
      return { data: payload as RaRecord };
    },
    async deleteMany(resource, params) {
      await Promise.all(params.ids.map(async (id) => provider.delete(resource, { id, previousData: {} as RaRecord })));
      return { data: params.ids };
    },
  };

  return provider;
}
