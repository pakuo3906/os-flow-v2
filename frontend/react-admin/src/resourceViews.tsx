import { useMemo, useState } from "react";
import {
  BooleanField,
  Button,
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
  SaveButton,
  SelectInput,
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
  useResourceContext,
} from "react-admin";
import { apiUrl, fetchJson } from "./api";
import type { ManifestField, ManifestResource } from "./types";

function filterComponent(filterName: string) {
  switch (filterName) {
    case "query":
      return <TextInput source="query" alwaysOn />;
    case "case_id":
      return <NumberInput source="case_id" />;
    case "document_id":
      return <NumberInput source="document_id" />;
    case "status":
      return <TextInput source="status" />;
    case "due_before":
      return <DateInput source="due_before" />;
    case "created_after":
      return <DateInput source="created_after" />;
    case "created_before":
      return <DateInput source="created_before" />;
    case "invoice_status":
      return <TextInput source="invoice_status" />;
    case "output_status":
      return <TextInput source="output_status" />;
    case "deliver_to":
      return <TextInput source="deliver_to" />;
    case "source_type":
      return <TextInput source="source_type" />;
    default:
      return <TextInput source={filterName} />;
  }
}

function fieldRenderer(resource: ManifestResource, fieldName: string) {
  if (fieldName === "id") {
    return <TextField key={fieldName} source={fieldName} />;
  }
  if (fieldName === "created_at" || fieldName === "updated_at" || fieldName.endsWith("_at")) {
    return <DateField key={fieldName} source={fieldName} showTime />;
  }
  if (fieldName === "extraction") {
    return (
      <FunctionField
        key={fieldName}
        label="extraction"
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
  if (fieldName === "status" || fieldName.endsWith("_status")) {
    return <TextField key={fieldName} source={fieldName} />;
  }
  if (resource.name === "notification_deliveries" && fieldName === "delivered_count") {
    return <NumberInput key={fieldName} source={fieldName} disabled />;
  }
  return <TextField key={fieldName} source={fieldName} />;
}

function fieldInput(field: ManifestField) {
  switch (field.input_type) {
    case "date":
      return <DateInput key={field.name} source={field.name} label={field.label || field.name} />;
    case "number":
      return <NumberInput key={field.name} source={field.name} label={field.label || field.name} />;
    case "select":
      return <SelectInput key={field.name} source={field.name} label={field.label || field.name} choices={[]} />;
    default:
      return <TextInput key={field.name} source={field.name} label={field.label || field.name} />;
  }
}

export function buildFilters(resource: ManifestResource) {
  return <Filter>{(resource.filters || []).map((filterName) => filterComponent(filterName))}</Filter>;
}

export function ManifestList() {
  const resource = useResourceContext<ManifestResource>();
  const listFields = resource?.fields || [];
  const hasEdit = Boolean(resource?.editPath);
  return (
    <List
      filters={buildFilters(resource)}
      sort={resource?.sort ?? { field: "updated_at", order: "DESC" }}
      perPage={25}
    >
      <Datagrid rowClick="show">
        {listFields.map((fieldName) => fieldRenderer(resource, fieldName))}
        <ShowButton />
        {hasEdit ? <EditButton /> : null}
        {resource?.name === "cases" ? <CaseReprocessButton /> : null}
      </Datagrid>
    </List>
  );
}

export function ManifestShow() {
  const resource = useResourceContext<ManifestResource>();
  const fields = resource?.detailFields || resource?.fields || [];
  return (
    <Show>
      <SimpleShowLayout>
        {fields.map((fieldName) => fieldRenderer(resource, fieldName))}
        <ResourceActionPanel />
      </SimpleShowLayout>
    </Show>
  );
}

export function ManifestEdit() {
  const resource = useResourceContext<ManifestResource>();
  const fields = resource?.formFields || [];
  return (
    <Edit>
      <SimpleForm toolbar={<Toolbar><SaveButton /><DeleteButton /></Toolbar>}>
        {fields.map((field) => fieldInput(field))}
      </SimpleForm>
    </Edit>
  );
}

function CaseReprocessButton() {
  const record = useRecordContext();
  const notify = useNotify();
  const refresh = useRefresh();
  const [busy, setBusy] = useState(false);
  if (!record) {
    return null;
  }
  const caseId = record.id;
  const handleClick = async () => {
    setBusy(true);
    try {
      const response = await fetch(apiUrl(`/cases/${caseId}/reprocess-documents`), { method: "POST" });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(typeof payload?.detail === "string" ? payload.detail : "Failed to reprocess case.");
      }
      notify(`Reprocessed case ${caseId}.`, { type: "info" });
      refresh();
    } catch (error) {
      notify(error instanceof Error ? error.message : "Failed to reprocess case.", { type: "warning" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button label={busy ? "Reprocessing..." : "Reprocess"} onClick={handleClick} disabled={busy} />
  );
}

function ResourceActionPanel() {
  const resource = useResourceContext<ManifestResource>();
  const record = useRecordContext();
  const notify = useNotify();
  const refresh = useRefresh();
  const [targetCaseId, setTargetCaseId] = useState<string>("");
  const [busyAction, setBusyAction] = useState<string | null>(null);

  if (!resource || !record) {
    return null;
  }

  const runAction = async (action: string, path?: string, init?: RequestInit) => {
    if (!path) {
      return;
    }
    setBusyAction(action);
    try {
      const response = await fetch(apiUrl(path), init);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `${action} failed`);
      }
      notify(`${action} succeeded.`, { type: "info" });
      refresh();
    } catch (error) {
      notify(error instanceof Error ? error.message : `${action} failed`, { type: "warning" });
    } finally {
      setBusyAction(null);
    }
  };

  if (resource.name === "documents") {
    return (
      <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
        <h3>Document actions</h3>
        <label>
          Target case ID
          <input value={targetCaseId} onChange={(event) => setTargetCaseId(event.target.value)} style={{ marginLeft: 8 }} />
        </label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            disabled={busyAction === "reassign" || !targetCaseId}
            onClick={() =>
              runAction(
                "reassign",
                `/documents/${record.id}/reassign`,
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
            disabled={busyAction === "reprocess"}
            onClick={() => runAction("reprocess", `/documents/${record.id}/reprocess`, { method: "POST" })}
          >
            Reprocess
          </button>
          <button
            type="button"
            disabled={busyAction === "delete"}
            onClick={() => runAction("delete", `/documents/${record.id}`, { method: "DELETE" })}
          >
            Delete
          </button>
        </div>
      </div>
    );
  }

  if (resource.name === "cases") {
    return (
      <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
        <h3>Case actions</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            disabled={busyAction === "reprocess"}
            onClick={() => runAction("reprocess", `/cases/${record.id}/reprocess-documents`, { method: "POST" })}
          >
            Reprocess documents
          </button>
        </div>
      </div>
    );
  }

  return null;
}
