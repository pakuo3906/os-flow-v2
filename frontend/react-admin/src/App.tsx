import { useEffect, useMemo, useState } from "react";
import { Admin, Resource, Loading, Error as RaError } from "react-admin";
import { createManifestDataProvider } from "./dataProvider";
import { loadManifest } from "./manifest";
import type { ManifestResource } from "./types";
import { ManifestEdit, ManifestList, ManifestShow } from "./resourceViews";

export default function App() {
  const [resources, setResources] = useState<ManifestResource[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadManifest()
      .then((payload) => setResources(payload.resources))
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : String(loadError));
      });
  }, []);

  const dataProvider = useMemo(() => {
    if (!resources) {
      return null;
    }
    return createManifestDataProvider(resources);
  }, [resources]);

  if (error) {
    return <RaError error={error instanceof Error ? error : new Error(error)} />;
  }

  if (!resources || !dataProvider) {
    return <Loading />;
  }

  return (
    <Admin dataProvider={dataProvider} title="O's flow React-admin">
      {resources.map((resource) => {
        const supportsEdit = Boolean(resource.editPath) && (resource.supports || []).includes("edit");
        return (
          <Resource
            key={resource.name}
            name={resource.name}
            list={ManifestList}
            show={ManifestShow}
            edit={supportsEdit ? ManifestEdit : undefined}
          />
        );
      })}
    </Admin>
  );
}
