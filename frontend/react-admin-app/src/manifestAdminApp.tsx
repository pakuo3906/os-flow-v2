import { useEffect, useMemo, useState } from "react";
import {
  Admin,
  Datagrid,
  DateField,
  DateInput,
  DeleteButton,
  Edit,
  EditButton,
  Filter,
  FunctionField,
  List,
  NumberInput,
  Resource,
  SaveButton,
  Show,
  ShowButton,
  SimpleForm,
  SimpleShowLayout,
  TextField,
  TextInput,
  Toolbar,
  useNotify,
  useRecordContext,
  useRefresh,
} from "react-admin";
import { apiUrl, fetchJson } from "./api";
import { createManifestDataProvider } from "./dataProvider";
import { loadManifest } from "./manifest";
import type { ManifestField, ManifestResource, RecordLike } from "./types";

function manifestFilter(fieldName: string) {
  switch (fieldName) {
    case "query":
      return <TextInput key={fieldName} source={fieldName} alwaysOn />;
    case "case_id":
    case "document_id":
      return <NumberInput key={fieldName} source={fieldName} />;
    case "due_before":
    case "created_after":
    case "created_before":
      return <DateInput key={fieldName} source={fieldName} />;
    default:
      return <TextInput key={fieldName} source={fieldName} />;
  }
}

function manifestField(fieldName: string) {
  if (fieldName === "extraction") {
    return (
      <FunctionField
        key={fieldName}
        source={fieldName}
        render={(record: Record<string, unknown>) => {
          const extraction = (record.extraction as Record<string, unknown> | undefined) || {};
          if (extraction.available) {
            return `${extraction.extraction_source || "unknown"} via ${extraction.extraction_engine || "unknown"}`;
          }
          return "no extraction snapshot";
        }}
      />
    );
  }
  if (fieldName === "missing_submission_reason") {
    return <TextField key={fieldName} source={fieldName} />;
  }
  if (fieldName.endsWith("_at")) {
    return <DateField key={fieldName} source={fieldName} showTime />;
  }
  return <TextField key={fieldName} source={fieldName} />;
}

function manifestInput(field: ManifestField) {
  switch (field.input_type) {
    case "date":
      return <DateInput key={field.name} source={field.name} label={field.label || field.name} />;
    case "number":
      return <NumberInput key={field.name} source={field.name} label={field.label || field.name} />;
    default:
      return <TextInput key={field.name} source={field.name} label={field.label || field.name} />;
  }
}

function createFilters(resource: ManifestResource) {
  return <Filter>{(resource.filters || []).map(manifestFilter)}</Filter>;
}

function createList(resource: ManifestResource) {
  return function ResourceList() {
    return (
      <List filters={createFilters(resource)} sort={resource.sort ?? { field: "updated_at", order: "DESC" }} perPage={25}>
        <Datagrid rowClick="show">
          {(resource.fields || []).map(manifestField)}
          <ShowButton />
          {resource.editPath ? <EditButton /> : null}
        </Datagrid>
      </List>
    );
  };
}

function createShow(resource: ManifestResource) {
  return function ResourceShow() {
    return (
      <Show>
        <SimpleShowLayout>
          {(resource.detailFields || resource.fields || []).map(manifestField)}
          <ResourceActionPanel resource={resource} />
        </SimpleShowLayout>
      </Show>
    );
  };
}

function createEdit(resource: ManifestResource) {
  return function ResourceEdit() {
    return (
      <Edit>
        <SimpleForm toolbar={<Toolbar><SaveButton /><DeleteButton /></Toolbar>}>
          {(resource.formFields || []).map(manifestInput)}
        </SimpleForm>
      </Edit>
    );
  };
}

function ResourceActionPanel({ resource }: { resource: ManifestResource }) {
  const record = useRecordContext<RecordLike>();
  const notify = useNotify();
  const refresh = useRefresh();
  const [targetCaseId, setTargetCaseId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  if (!record) {
    return null;
  }

  async function runAction(action: string, path: string, init?: RequestInit) {
    setBusy(action);
    try {
      const response = await fetchJson(path, init);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `${action} failed`);
      }
      notify(`${action} succeeded.`, { type: "info" });
      refresh();
    } catch (error) {
      notify(error instanceof Error ? error.message : `${action} failed`, { type: "warning" });
    } finally {
      setBusy(null);
    }
  }

  if (resource.name === "cases") {
    return (
      <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
        <h3>Case actions</h3>
        <button
          type="button"
          disabled={busy === "reprocess"}
          onClick={() => runAction("reprocess", apiUrl(`/cases/${record.id}/reprocess-documents`), { method: "POST" })}
        >
          Reprocess documents
        </button>
      </div>
    );
  }

  if (resource.name === "documents") {
    return (
      <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
        <h3>Document actions</h3>
        <label style={{ display: "grid", gap: 4 }}>
          <span>Target case ID</span>
          <input value={targetCaseId} onChange={(event) => setTargetCaseId(event.target.value)} />
        </label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            disabled={busy === "reassign" || !targetCaseId}
            onClick={() =>
              runAction(
                "reassign",
                apiUrl(`/documents/${record.id}/reassign`),
                {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ target_case_id: Number(targetCaseId) }),
                },
              )
            }
          >
            Reassign
          </button>
          <button
            type="button"
            disabled={busy === "reprocess"}
            onClick={() => runAction("reprocess", apiUrl(`/documents/${record.id}/reprocess`), { method: "POST" })}
          >
            Reprocess
          </button>
          <button
            type="button"
            disabled={busy === "delete"}
            onClick={() => runAction("delete", apiUrl(`/documents/${record.id}`), { method: "DELETE" })}
          >
            Delete
          </button>
        </div>
      </div>
    );
  }

  return null;
}

function AdminShell({ resources, dataProvider }: { resources: ManifestResource[]; dataProvider: ReturnType<typeof createManifestDataProvider> }) {
  return (
    <Admin dataProvider={dataProvider} title="O's flow React-admin">
      {resources.map((resource) => {
        const ListView = createList(resource);
        const ShowView = createShow(resource);
        const EditView = resource.editPath ? createEdit(resource) : undefined;
        return <Resource key={resource.name} name={resource.name} list={ListView} show={ShowView} edit={EditView} />;
      })}
    </Admin>
  );
}

export default function ManifestAdminApp() {
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
    return <div style={{ padding: 24 }}>Failed to load manifest: {error}</div>;
  }

  if (!resources || !dataProvider) {
    return <div style={{ padding: 24 }}>Loading React-admin manifest...</div>;
  }

  return <AdminShell resources={resources} dataProvider={dataProvider} />;
}
