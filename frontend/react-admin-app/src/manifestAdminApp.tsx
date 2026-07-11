import { useEffect, useMemo, useState } from "react";
import {
  Admin,
  Datagrid,
  CustomRoutes,
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
import { Link, Route } from "react-router-dom";
import { apiUrl, fetchJson } from "./api";
import { createManifestDataProvider } from "./dataProvider";
import { loadManifest } from "./manifest";
import type { ManifestField, ManifestResource, RecordLike } from "./types";

type DashboardPayload = {
  settings?: {
    app_env?: string;
    repository_backend?: string;
    storage_backend?: string;
    insforge?: {
      repository_ready?: boolean;
      storage_ready?: boolean;
      repository_missing?: string[];
      storage_missing?: string[];
    };
    auth?: {
      ready?: boolean;
      jwks_url_configured?: boolean;
      issuer_url_configured?: boolean;
      audience_configured?: boolean;
      missing?: string[];
    };
    customer?: {
      ready?: boolean;
      default_slug_configured?: boolean;
      default_name_configured?: boolean;
      missing?: string[];
    };
    customer_scope?: {
      source?: string;
      header_slug?: string | null;
      header_name?: string | null;
      effective_slug?: string | null;
      effective_name?: string | null;
      default_slug?: string | null;
      default_name?: string | null;
      ready?: boolean;
    };
  };
  billing?: {
    invoice_count?: number;
    missing_submission_count?: number;
  };
  summary?: {
    cases_total?: number;
    documents_total?: number;
    documents_active?: number;
    processing_jobs_total?: number;
    operation_logs_total?: number;
    notification_deliveries_total?: number;
    rag_entries_total?: number;
  };
  recent?: {
    cases?: Array<{ id: number; case_code: string; title?: string; updated_at?: string }>;
    documents?: Array<{ id: number; filename?: string; updated_at?: string }>;
    operation_logs?: Array<{ id: number; event_type?: string; message?: string; created_at?: string }>;
  };
  activity?: {
    items?: Array<{ id: number; kind?: string; title?: string; created_at?: string }>;
  };
  notifications?: {
    total?: number;
    success_total?: number;
    failed_total?: number;
    failure_rate?: number;
    needs_attention?: boolean;
    attention_reason?: string | null;
    attention_targets?: string[];
    latest_delivery?: string | null;
    latest_success?: string | null;
    latest_failure?: string | null;
  };
};

type ReadinessPayload = {
  app_env?: string;
  repository_backend?: string;
  storage_backend?: string;
  insforge?: {
    repository_ready?: boolean;
    storage_ready?: boolean;
    repository_missing?: string[];
    storage_missing?: string[];
  };
  auth?: {
    ready?: boolean;
    jwks_url_configured?: boolean;
    issuer_url_configured?: boolean;
    audience_configured?: boolean;
    missing?: string[];
  };
  customer?: {
    ready?: boolean;
    default_slug_configured?: boolean;
    default_name_configured?: boolean;
    missing?: string[];
  };
  customer_scope?: {
    source?: string;
    header_slug?: string | null;
    header_name?: string | null;
    effective_slug?: string | null;
    effective_name?: string | null;
    default_slug?: string | null;
    default_name?: string | null;
    ready?: boolean;
  };
  billing?: {
    invoice_count?: number;
    missing_submission_count?: number;
  };
  extraction?: Record<string, boolean>;
};


type DemoPackPayload = {
  title?: string;
  scenario?: string;
  seed_script?: string;
  resources?: Array<{ label?: string; path?: string; description?: string }>;
  current_counts?: Record<string, number>;
  missing_submissions_preview?: Array<Record<string, unknown>>;
};
type BillingInvoiceRecord = {
  id?: number;
  case_id?: number;
  case_code?: string;
  title?: string;
  due_date?: string | null;
  invoice_status?: string;
  output_status?: string;
  updated_at?: string;
};

type MissingSubmissionRecord = {
  case_id?: number;
  case_code?: string;
  title?: string;
  due_date?: string | null;
  invoice_status?: string;
  output_status?: string;
  missing_submission_reason?: string;
  updated_at?: string;
};

function countValue(value: number | undefined) {
  return typeof value === "number" ? value : 0;
}

function DashboardCard({
  title,
  value,
  note,
}: {
  title: string;
  value: string;
  note?: string;
}) {
  return (
    <section
      style={{
        border: "1px solid #d7dde8",
        borderRadius: 16,
        padding: 16,
        background: "linear-gradient(180deg, #ffffff 0%, #f7f9fc 100%)",
        boxShadow: "0 8px 24px rgba(15, 23, 42, 0.06)",
      }}
    >
      <div style={{ color: "#667085", fontSize: 12, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase" }}>
        {title}
      </div>
      <div style={{ marginTop: 8, fontSize: 28, fontWeight: 800, color: "#101828" }}>{value}</div>
      {note ? <div style={{ marginTop: 8, color: "#475467", fontSize: 13, lineHeight: 1.5 }}>{note}</div> : null}
    </section>
  );
}

function DashboardList({
  title,
  items,
  emptyLabel,
}: {
  title: string;
  items: string[];
  emptyLabel: string;
}) {
  return (
    <section
      style={{
        border: "1px solid #d7dde8",
        borderRadius: 16,
        padding: 16,
        background: "#ffffff",
        boxShadow: "0 8px 24px rgba(15, 23, 42, 0.04)",
      }}
    >
      <div style={{ fontSize: 16, fontWeight: 700, color: "#101828", marginBottom: 12 }}>{title}</div>
      {items.length > 0 ? (
        <ul style={{ margin: 0, paddingLeft: 18, color: "#344054", lineHeight: 1.65 }}>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <div style={{ color: "#667085" }}>{emptyLabel}</div>
      )}
    </section>
  );
}


function navButtonStyle(background = "#ffffff", color = "#101828") {
  return {
    color,
    background,
    padding: "10px 14px",
    borderRadius: 999,
    textDecoration: "none",
    fontWeight: 700,
    border: "1px solid rgba(16, 24, 40, 0.08)",
  } as const;
}

function NavButton({ to, children }: { to: string; children: string }) {
  return (
    <Link to={to} style={navButtonStyle()}>
      {children}
    </Link>
  );
}

function formatMissing(values?: string[]) {
  return (values || []).length > 0 ? (values || []).join(", ") : "none";
}

function StatusPage({ title, endpoint }: { title: string; endpoint: string }) {
  const [payload, setPayload] = useState<ReadinessPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch(apiUrl(endpoint))
      .then(async (response) => {
        const body = (await response.json()) as ReadinessPayload;
        if (!response.ok) {
          throw new Error("Failed to load status page");
        }
        if (active) {
          setPayload(body);
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      });
    return () => {
      active = false;
    };
  }, [endpoint]);

  if (error) {
    return <div style={{ padding: 24 }}>Failed to load {title.toLowerCase()}: {error}</div>;
  }

  if (!payload) {
    return <div style={{ padding: 24 }}>Loading {title.toLowerCase()}...</div>;
  }

  const settings = payload;
  const insforge = settings.insforge ?? {};
  const auth = settings.auth ?? {};
  const customer = settings.customer ?? {};
  const customerScope = settings.customer_scope ?? {};

  return (
    <div style={{ display: "grid", gap: 20, padding: 20 }}>
      <header
        style={{
          borderRadius: 20,
          padding: 24,
          color: "#ffffff",
          background: "linear-gradient(135deg, #101828 0%, #1d4ed8 100%)",
          boxShadow: "0 16px 40px rgba(15, 23, 42, 0.18)",
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", opacity: 0.8 }}>
          Oz flow Admin
        </div>
        <div style={{ marginTop: 8, fontSize: 32, fontWeight: 800, lineHeight: 1.1 }}>{title}</div>
        <div style={{ marginTop: 10, maxWidth: 760, fontSize: 15, lineHeight: 1.7, opacity: 0.92 }}>
          Direct status view for backend readiness. This is where we keep checking the InsForge foundation and the auth/customer placeholder setup.
        </div>
      </header>

      <section style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <DashboardList
          title="Core backend"
          items={[
            `Environment: ${settings.app_env ?? "unknown"}`,
            `Repository: ${settings.repository_backend ?? "unknown"}`,
            `Storage: ${settings.storage_backend ?? "unknown"}`,
          ]}
          emptyLabel="No core backend settings found."
        />
        <DashboardList
          title="InsForge"
          items={[
            `Repository ready: ${insforge.repository_ready ? "yes" : "no"}`,
            `Storage ready: ${insforge.storage_ready ? "yes" : "no"}`,
            `Missing repository: ${formatMissing(insforge.repository_missing)}`,
            `Missing storage: ${formatMissing(insforge.storage_missing)}`,
          ]}
          emptyLabel="No InsForge settings found."
        />
        <DashboardList
          title="Auth"
          items={[
            `Ready: ${auth.ready ? "yes" : "no"}`,
            `JWKS configured: ${auth.jwks_url_configured ? "yes" : "no"}`,
            `Issuer configured: ${auth.issuer_url_configured ? "yes" : "no"}`,
            `Audience configured: ${auth.audience_configured ? "yes" : "no"}`,
            `Missing: ${formatMissing(auth.missing)}`,
          ]}
          emptyLabel="No auth settings found."
        />
        <DashboardList
          title="Customer"
          items={[
            `Ready: ${customer.ready ? "yes" : "no"}`,
            `Default slug configured: ${customer.default_slug_configured ? "yes" : "no"}`,
            `Default name configured: ${customer.default_name_configured ? "yes" : "no"}`,
            `Effective scope: ${customerScope.effective_slug ?? "unset"} / ${customerScope.effective_name ?? "unset"}`,
            `Source: ${customerScope.source ?? "unset"}`,
            `Missing: ${formatMissing(customer.missing)}`,
          ]}
          emptyLabel="No customer settings found."
        />
      </section>
    </div>
  );
}


function DemoPackPage() {
  const [payload, setPayload] = useState<DemoPackPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch(apiUrl("/admin/demo-pack"))
      .then(async (response) => {
        const body = (await response.json()) as DemoPackPayload;
        if (!response.ok) {
          throw new Error("Failed to load demo pack");
        }
        if (active) {
          setPayload(body);
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (error) {
    return <div style={{ padding: 24 }}>Failed to load demo pack: {error}</div>;
  }

  if (!payload) {
    return <div style={{ padding: 24 }}>Loading demo pack...</div>;
  }

  const resources = payload.resources ?? [];
  const currentCounts = payload.current_counts ?? {};
  const preview = payload.missing_submissions_preview ?? [];

  return (
    <div style={{ display: "grid", gap: 20, padding: 20 }}>
      <header
        style={{
          borderRadius: 20,
          padding: 24,
          color: "#ffffff",
          background: "linear-gradient(135deg, #111827 0%, #7c3aed 100%)",
          boxShadow: "0 16px 40px rgba(15, 23, 42, 0.18)",
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", opacity: 0.8 }}>
          Oz flow Admin
        </div>
        <div style={{ marginTop: 8, fontSize: 32, fontWeight: 800, lineHeight: 1.1 }}>{payload.title ?? "Demo pack"}</div>
        <div style={{ marginTop: 10, maxWidth: 760, fontSize: 15, lineHeight: 1.7, opacity: 0.92 }}>
          Use this path when you want to seed a reviewable sample flow first, then touch the case, document, and billing
          surfaces with real-ish data.
        </div>
        <div style={{ marginTop: 18, display: "flex", gap: 12, flexWrap: "wrap" }}>
          <NavButton to="/">Dashboard</NavButton>
          <NavButton to="/billing">Billing / output</NavButton>
          <NavButton to="/backends">Backend status</NavButton>
        </div>
      </header>

      <section style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        {Object.entries(currentCounts).map(([key, value]) => (
          <DashboardCard key={key} title={key.replace(/_/g, " ")} value={String(value)} note="Current demo pack count." />
        ))}
      </section>

      <section style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <DashboardList
          title="Scenario"
          items={[payload.scenario ?? "No scenario text found.", payload.seed_script ? `Seed script: ${payload.seed_script}` : "Seed script unavailable."]}
          emptyLabel="No demo scenario available."
        />
        <DashboardList
          title="Review targets"
          items={resources.map((resource) => `${resource.label ?? resource.path ?? "resource"}${resource.path ? ` - ${resource.path}` : ""}`)}
          emptyLabel="No review targets found."
        />
      </section>

      <section style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <section style={{ border: "1px solid #d7dde8", borderRadius: 16, padding: 16, background: "#ffffff", boxShadow: "0 8px 24px rgba(15, 23, 42, 0.04)" }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: "#101828", marginBottom: 12 }}>Seed command</div>
          <pre style={{ margin: 0, padding: 16, background: "#f8fafc", borderRadius: 12, overflowX: "auto", color: "#1d2939" }}>
            {payload.seed_script ?? "Not available"}
          </pre>
        </section>
        <section style={{ border: "1px solid #d7dde8", borderRadius: 16, padding: 16, background: "#ffffff", boxShadow: "0 8px 24px rgba(15, 23, 42, 0.04)" }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: "#101828", marginBottom: 12 }}>Missing submissions preview</div>
          {preview.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18, color: "#344054", lineHeight: 1.65 }}>
              {preview.map((item, index) => (
                <li key={index}>{JSON.stringify(item)}</li>
              ))}
            </ul>
          ) : (
            <div style={{ color: "#667085" }}>No preview data yet.</div>
          )}
        </section>
      </section>
    </div>
  );
}

function BillingRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, borderBottom: "1px solid #eaecf0", padding: "8px 0" }}>
      <span style={{ color: "#475467", fontWeight: 600 }}>{label}</span>
      <span style={{ color: "#101828", fontWeight: 700 }}>{value}</span>
    </div>
  );
}

function BillingPage() {
  const [invoices, setInvoices] = useState<BillingInvoiceRecord[] | null>(null);
  const [missing, setMissing] = useState<MissingSubmissionRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch(apiUrl('/admin/invoices')).then(async (response) => {
        const body = (await response.json()) as BillingInvoiceRecord[];
        if (!response.ok) throw new Error('Failed to load invoices');
        return body;
      }),
      fetch(apiUrl('/admin/missing-submissions')).then(async (response) => {
        const body = await response.json() as { items?: MissingSubmissionRecord[] };
        if (!response.ok) throw new Error('Failed to load missing submissions');
        return Array.isArray(body.items) ? body.items : [];
      }),
    ])
      .then(([invoiceItems, missingItems]) => {
        if (active) {
          setInvoices(invoiceItems);
          setMissing(missingItems);
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (error) {
    return <div style={{ padding: 24 }}>Failed to load billing/output: {error}</div>;
  }

  if (!invoices || !missing) {
    return <div style={{ padding: 24 }}>Loading billing/output...</div>;
  }

  const pendingInvoices = invoices.filter((item) => (item.invoice_status || '').toLowerCase() !== 'unbilled');
  const pendingOutput = invoices.filter((item) => (item.output_status || '').toLowerCase() !== 'completed');

  return (
    <div style={{ display: 'grid', gap: 20, padding: 20 }}>
      <header
        style={{
          borderRadius: 20,
          padding: 24,
          color: '#ffffff',
          background: 'linear-gradient(135deg, #111827 0%, #0f766e 100%)',
          boxShadow: '0 16px 40px rgba(15, 23, 42, 0.18)',
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', opacity: 0.8 }}>
          Oz flow Admin
        </div>
        <div style={{ marginTop: 8, fontSize: 32, fontWeight: 800, lineHeight: 1.1 }}>Billing / Output</div>
        <div style={{ marginTop: 10, maxWidth: 760, fontSize: 15, lineHeight: 1.7, opacity: 0.92 }}>
          Quick view for invoices, output completion, and missing submissions. This is intentionally thin so the operational flow stays aligned with the ledger.
        </div>
      </header>

      <section style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
        <DashboardCard title="Invoices" value={String(invoices.length)} note="Current invoice list from the admin endpoint." />
        <DashboardCard title="Missing" value={String(missing.length)} note="Cases needing billing or output attention." />
        <DashboardCard title="Invoice attention" value={String(pendingInvoices.length)} note="Invoices not still in unbilled state." />
        <DashboardCard title="Output attention" value={String(pendingOutput.length)} note="Cases not yet completed for output." />
      </section>

      <section style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
        <DashboardList
          title="Invoices"
          items={invoices.slice(0, 10).map((item) => `${item.case_code ?? item.id ?? 'unknown'} - ${item.invoice_status ?? 'unknown'} / ${item.output_status ?? 'unknown'}${item.due_date ? ` / due ${item.due_date}` : ''}`)}
          emptyLabel="No invoices yet."
        />
        <DashboardList
          title="Missing submissions"
          items={missing.slice(0, 10).map((item) => `${item.case_code ?? 'unknown'} - ${item.missing_submission_reason ?? 'missing submission'}`)}
          emptyLabel="No missing submissions yet."
        />
      </section>

      <section style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
        <section style={{ border: '1px solid #d7dde8', borderRadius: 16, padding: 16, background: '#ffffff', boxShadow: '0 8px 24px rgba(15, 23, 42, 0.04)' }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#101828', marginBottom: 12 }}>Invoice detail snapshot</div>
          {invoices[0] ? (
            <>
              <BillingRow label="Case" value={invoices[0].case_code ?? 'unknown'} />
              <BillingRow label="Invoice status" value={invoices[0].invoice_status ?? 'unknown'} />
              <BillingRow label="Output status" value={invoices[0].output_status ?? 'unknown'} />
              <BillingRow label="Due date" value={invoices[0].due_date ?? 'unknown'} />
            </>
          ) : (
            <div style={{ color: '#667085' }}>No invoice data available.</div>
          )}
        </section>
        <section style={{ border: '1px solid #d7dde8', borderRadius: 16, padding: 16, background: '#ffffff', boxShadow: '0 8px 24px rgba(15, 23, 42, 0.04)' }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#101828', marginBottom: 12 }}>Missing submission snapshot</div>
          {missing[0] ? (
            <>
              <BillingRow label="Case" value={missing[0].case_code ?? 'unknown'} />
              <BillingRow label="Reason" value={missing[0].missing_submission_reason ?? 'unknown'} />
              <BillingRow label="Invoice" value={missing[0].invoice_status ?? 'unknown'} />
              <BillingRow label="Output" value={missing[0].output_status ?? 'unknown'} />
            </>
          ) : (
            <div style={{ color: '#667085' }}>No missing submission data available.</div>
          )}
        </section>
      </section>
    </div>
  );
}

function DashboardHome() {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch(apiUrl("/admin/dashboard"))
      .then(async (response) => {
        const body = (await response.json()) as DashboardPayload;
        if (!response.ok) {
          throw new Error("Failed to load dashboard");
        }
        if (active) {
          setPayload(body);
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (error) {
    return <div style={{ padding: 24 }}>Failed to load admin dashboard: {error}</div>;
  }

  if (!payload) {
    return <div style={{ padding: 24 }}>Loading admin dashboard...</div>;
  }

  const summary = payload.summary ?? {};
  const settings = payload.settings ?? {};
  const insforge = settings.insforge ?? {};
  const notifications = payload.notifications ?? {};
  const auth = settings.auth ?? {};
  const customer = settings.customer ?? {};
  const customerScope = settings.customer_scope ?? {};
  const billing = payload.billing ?? {};
  const recentCases = (payload.recent?.cases ?? []).slice(0, 3);
  const recentDocuments = (payload.recent?.documents ?? []).slice(0, 3);
  const recentActivity = (payload.activity?.items ?? []).slice(0, 5);

  return (
    <div style={{ display: "grid", gap: 20, padding: 20 }}>
      <header
        style={{
          borderRadius: 20,
          padding: 24,
          color: "#ffffff",
          background: "linear-gradient(135deg, #101828 0%, #1d4ed8 100%)",
          boxShadow: "0 16px 40px rgba(15, 23, 42, 0.18)",
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", opacity: 0.8 }}>
          Oz flow Admin
        </div>
        <div style={{ marginTop: 8, fontSize: 32, fontWeight: 800, lineHeight: 1.1 }}>Operational overview</div>
        <div style={{ marginTop: 10, maxWidth: 760, fontSize: 15, lineHeight: 1.7, opacity: 0.92 }}>
          Local development is using the SQLite/local adapters. This dashboard mirrors the backend summary so we can move
          toward InsForge, auth, billing/output, and operations without losing the current ledger model.
        </div>
        <div style={{ marginTop: 18, display: "flex", gap: 12, flexWrap: "wrap" }}>
          <NavButton to="/demo-pack">Demo pack</NavButton>
          <NavButton to="/backends">Backend status</NavButton>
          <NavButton to="/auth">Auth status</NavButton>
          <NavButton to="/billing">Billing / output</NavButton>
        </div>
      </header>

      <section style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        <DashboardCard title="Cases" value={String(countValue(summary.cases_total))} note="Total tracked case records." />
        <DashboardCard title="Documents" value={String(countValue(summary.documents_total))} note={`${countValue(summary.documents_active)} active documents.`} />
        <DashboardCard title="Jobs" value={String(countValue(summary.processing_jobs_total))} note="Processing jobs in the ledger." />
        <DashboardCard title="Notifications" value={String(countValue(summary.notification_deliveries_total))} note={`${countValue(notifications.failed_total)} failed deliveries.`} />
        <DashboardCard title="RAG Entries" value={String(countValue(summary.rag_entries_total))} note="Reusable extracted artifacts." />
      </section>

      <section style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <DashboardList
          title="Backend readiness"
          items={[
            `Environment: ${settings.app_env ?? "unknown"}`,
            `Repository: ${settings.repository_backend ?? "unknown"}`,
            `Storage: ${settings.storage_backend ?? "unknown"}`,
            `InsForge repository ready: ${insforge.repository_ready ? "yes" : "no"}`,
            `InsForge storage ready: ${insforge.storage_ready ? "yes" : "no"}`,
            `Auth ready: ${auth.ready ? "yes" : "no"}`,
            `Customer scope ready: ${customer.ready ? "yes" : "no"}`,
            `Effective customer slug: ${customerScope.effective_slug ?? "unset"}`,
            `Invoices in scope: ${countValue(billing.invoice_count)}`,
          ]}
          emptyLabel="No backend settings found."
        />
        <DashboardList
          title="Auth readiness"
          items={[
            `JWKS configured: ${auth.jwks_url_configured ? "yes" : "no"}`,
            `Issuer configured: ${auth.issuer_url_configured ? "yes" : "no"}`,
            `Audience configured: ${auth.audience_configured ? "yes" : "no"}`,
            auth.ready ? "Auth configuration looks complete." : `Missing: ${(auth.missing || []).join(", ") || "unknown"}`,
          ]}
          emptyLabel="No auth settings found."
        />
        <DashboardList
          title="Customer scope"
          items={[
            `Default slug configured: ${customer.default_slug_configured ? "yes" : "no"}`,
            `Default name configured: ${customer.default_name_configured ? "yes" : "no"}`,
            customer.ready ? "Customer placeholder settings are present." : `Missing: ${(customer.missing || []).join(", ") || "unknown"}`,
          ]}
          emptyLabel="No customer settings found."
        />
        <DashboardList
          title="Notification health"
          items={[
            `Total deliveries: ${countValue(notifications.total)}`,
            `Success: ${countValue(notifications.success_total)}`,
            `Failed: ${countValue(notifications.failed_total)}`,
            `Failure rate: ${typeof notifications.failure_rate === "number" ? `${(notifications.failure_rate * 100).toFixed(1)}%` : "n/a"}`,
            notifications.needs_attention ? `Attention: ${notifications.attention_reason ?? "delivery threshold reached"}` : "Attention: none",
          ]}
          emptyLabel="No notification data yet."
        />
      </section>

      <section style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <DashboardList
          title="Recent cases"
          items={recentCases.map((item) => `${item.case_code} ${item.title ? `- ${item.title}` : ""}`.trim())}
          emptyLabel="No cases yet."
        />
        <DashboardList
          title="Recent documents"
          items={recentDocuments.map((item) => item.filename ?? `Document #${item.id}`)}
          emptyLabel="No documents yet."
        />
        <DashboardList
          title="Recent activity"
          items={recentActivity.map((item) => `${item.kind ?? "activity"}${item.title ? ` - ${item.title}` : ""}`)}
          emptyLabel="No recent activity yet."
        />
      </section>
    </div>
  );
}

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
    <Admin dataProvider={dataProvider} title="Oz flow React-admin" dashboard={DashboardHome}>
      <CustomRoutes>
        <Route path="/demo-pack" element={<DemoPackPage />} />
        <Route path="/backends" element={<StatusPage title="Backend status" endpoint="/admin/backends" />} />
        <Route path="/auth" element={<StatusPage title="Auth status" endpoint="/admin/auth" />} />
        <Route path="/billing" element={<BillingPage />} />
      </CustomRoutes>
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


