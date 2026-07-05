export type ManifestField = {
  name: string;
  label?: string;
  input_type?: string;
  placeholder?: string;
};

export type ManifestResource = {
  name: string;
  label: string;
  idField: string;
  labelField?: string;
  sort?: { field: string; order: "ASC" | "DESC" };
  supports?: string[];
  actions?: string[];
  listPath?: string;
  showPath?: string;
  editPath?: string;
  activityPath?: string;
  summaryPath?: string;
  trendsPath?: string;
  alertsPath?: string;
  reportPath?: string;
  searchPath?: string;
  fields?: string[];
  detailFields?: string[];
  formFields?: ManifestField[];
  filters?: string[];
};

export type ManifestResponse = {
  framework: string;
  resources: ManifestResource[];
};

export type RecordLike = Record<string, unknown> & { id: string | number };
