import { Injectable, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpEvent, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable, catchError, of, tap, throwError, timeout } from 'rxjs';

export interface User { id: string; username: string; full_name: string; is_admin: boolean }
export interface AdminAccount extends User { created_at: string }
export interface AuthResponse { user: User; csrf_token: string }
export type Role = 'owner' | 'editor' | 'viewer' | 'admin';
export interface Project {
  id: string; name: string; description: string | null; owner_id: string; owner_username: string; created_at: string;
  updated_at: string; role: Role; allow_viewer_writes: boolean; published_version_id: string | null;
}
export interface Member { account_id: string; username: string; full_name: string; role: Role }
export interface ProjectVersion {
  id: string; created_at: string; created_by: string; entry_path?: string; csv_snapshot_id?: string | null;
  is_published?: boolean; is_working?: boolean; published_at?: string | null;
}
export interface CsvSnapshot { id: string; filename?: string; created_at: string; created_by?: string; row_count?: number; byte_size?: number; columns: string[] }
export interface CsvSnapshotPreview { columns: string[]; rows: string[][]; row_count: number; page: number; page_size: number; total_pages: number }
export interface ApiDatasetConnection {
  id: string;
  name: string;
  url: string;
  method: 'GET' | 'POST';
  headers: Record<string, string>;
  secret_headers: string[];
  body: Record<string, unknown> | null;
  body_saved: boolean;
  records_path: string | null;
  updated_at: string;
}
export interface ApiDatasetConfig {
  name: string;
  url: string;
  method: 'GET' | 'POST';
  headers: Record<string, string | null>;
  body: Record<string, unknown> | null;
  clear_body?: boolean;
  records_path: string | null;
}
export interface ApiDatasetPreview { columns: string[]; rows: Record<string, unknown>[]; row_count: number }
export type ApiDatasetScheduleFrequency = 'interval' | 'daily' | 'weekly' | 'monthly';
export type ApiDatasetPublishMode = 'draft' | 'auto_publish';
export interface ApiDatasetSchedule {
  connection_id: string;
  enabled: boolean;
  frequency: ApiDatasetScheduleFrequency;
  interval_minutes: number | null;
  local_time: string | null;
  day_of_month: number | null;
  weekdays: number[];
  timezone: string;
  base_version_id: string;
  publish_mode: ApiDatasetPublishMode;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}
export interface ApiDatasetScheduleConfig {
  enabled: boolean;
  frequency: ApiDatasetScheduleFrequency;
  interval_minutes?: number;
  local_time?: string;
  day_of_month?: number | null;
  weekdays?: number[];
  timezone: string;
  base_version_id: string;
  publish_mode: ApiDatasetPublishMode;
}
export type ApiDatasetRunStatus = 'queued' | 'running' | 'succeeded' | 'unchanged' | 'needs_review' | 'failed' | 'cancelled';
export interface ApiDatasetRun {
  id: string;
  status: ApiDatasetRunStatus;
  is_manual: boolean;
  scheduled_for: string;
  started_at: string | null;
  finished_at: string | null;
  attempt_count: number;
  version_id: string | null;
  snapshot_id: string | null;
  error_code: string | null;
  error_message: string | null;
  published: boolean;
  created_at: string;
}
export interface Publication { published_version_id:string; previous_version_id:string|null; action:string; published_at:string }
export interface SavedRecord { id: string; project_id: string; data: Record<string,unknown>; revision: number; created_by: string; created_at: string; updated_by: string; updated_at: string }
export interface ApprovedSource { id: string; source_key: string; display_name: string; environment: string; approved_catalogs: string[] }

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly csrf = signal<string | null>(null);
  readonly currentUser = signal<User | null>(null);
  constructor(private readonly http: HttpClient) {}

  private remember(response: AuthResponse): void {
    this.csrf.set(response.csrf_token);
    this.currentUser.set(response.user);
  }
  private headers(): HttpHeaders {
    const token = this.csrf();
    return token ? new HttpHeaders({ 'X-CSRF-Token': token }) : new HttpHeaders();
  }
  auth(): Observable<AuthResponse | null> {
    return this.http.get<AuthResponse>('/api/auth/me').pipe(
      timeout(10000),
      tap(result => this.remember(result)),
      catchError((error: HttpErrorResponse) => {
        if (error.status === 401) { this.csrf.set(null); this.currentUser.set(null); return of(null); }
        return throwError(() => error);
      })
    );
  }
  login(username: string, password: string): Observable<AuthResponse> {
    return this.http.post<AuthResponse>('/api/auth/login', { username, password }).pipe(tap(result => this.remember(result)));
  }
  register(username: string, fullName: string, password: string): Observable<AuthResponse> {
    return this.http.post<AuthResponse>('/api/auth/register', { username, full_name: fullName, password }).pipe(tap(result => this.remember(result)));
  }
  logout(): Observable<void> {
    return this.http.post<void>('/api/auth/logout', {}, { headers: this.headers() }).pipe(tap(() => { this.csrf.set(null); this.currentUser.set(null); }));
  }
  adminAccounts(): Observable<{accounts:AdminAccount[]}> { return this.http.get<{accounts:AdminAccount[]}>('/api/admin/accounts'); }
  resetAccountPassword(username:string,newPassword:string): Observable<{ok:boolean}> { return this.http.post<{ok:boolean}>(`/api/admin/accounts/${encodeURIComponent(username)}/reset-password`,{new_password:newPassword},{headers:this.headers()}); }
  listProjects(): Observable<{ owned: Project[]; shared: Project[] }> { return this.http.get<{ owned: Project[]; shared: Project[] }>('/api/projects'); }
  createProject(name: string, description: string): Observable<Project> {
    return this.http.post<Project>('/api/projects', description ? { name, description } : { name }, { headers: this.headers() });
  }
  getProject(projectId: string): Observable<Project> { return this.http.get<Project>(`/api/projects/${encodeURIComponent(projectId)}`); }
  updateProject(projectId: string, changes: { name?: string; description?: string; allow_viewer_writes?: boolean }): Observable<Project> {
    return this.http.patch<Project>(`/api/projects/${encodeURIComponent(projectId)}`, changes, { headers: this.headers() });
  }
  members(projectId: string): Observable<{ members: Member[] }> { return this.http.get<{ members: Member[] }>(`/api/projects/${encodeURIComponent(projectId)}/members`); }
  setMember(projectId: string, username: string, role: 'editor' | 'viewer'): Observable<Member> {
    return this.http.put<Member>(`/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(username)}`, { role }, { headers: this.headers() });
  }
  removeMember(projectId: string, username: string): Observable<void> {
    return this.http.delete<void>(`/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(username)}`, { headers: this.headers() });
  }
  versions(projectId: string): Observable<{ versions: ProjectVersion[] }> { return this.http.get<{ versions: ProjectVersion[] }>(`/api/projects/${encodeURIComponent(projectId)}/versions`); }
  published(projectId: string): Observable<{version:ProjectVersion|null;publication:Publication|null}> { return this.http.get<{version:ProjectVersion|null;publication:Publication|null}>(`/api/projects/${encodeURIComponent(projectId)}/published`); }
  uploadVersion(projectId: string, file: File, csv: File | null): Observable<HttpEvent<ProjectVersion>> {
    const body = new FormData(); body.append('package', file); if (csv) body.append('csv', csv);
    return this.http.post<ProjectVersion>(`/api/projects/${encodeURIComponent(projectId)}/versions`, body, { headers: this.headers(), reportProgress: true, observe: 'events' });
  }
  publish(projectId: string, versionId: string): Observable<unknown> {
    return this.http.post(`/api/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/publish`, {}, { headers: this.headers() });
  }
  rollback(projectId: string): Observable<unknown> {
    return this.http.post(`/api/projects/${encodeURIComponent(projectId)}/publish/rollback`, {}, { headers: this.headers() });
  }
  viewUrl(projectId: string, versionId: string): string {
    return `/api/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/view`;
  }
  viewGrant(projectId: string, versionId: string): Observable<{url:string;grant_token:string}> {
    return this.http.post<{url:string;grant_token:string}>(`/api/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/view-grant`, {}, {headers:this.headers()});
  }
  snapshots(projectId: string): Observable<{ snapshots: CsvSnapshot[] }> { return this.http.get<{ snapshots: CsvSnapshot[] }>(`/api/projects/${encodeURIComponent(projectId)}/csv/snapshots`); }
  previewSnapshot(projectId: string, snapshotId: string, page = 1, pageSize = 10): Observable<CsvSnapshotPreview> {
    const params = new HttpParams().set('page', page).set('page_size', pageSize);
    return this.http.get<CsvSnapshotPreview>(`/api/projects/${encodeURIComponent(projectId)}/csv/snapshots/${encodeURIComponent(snapshotId)}/preview`, { params });
  }
  replaceCsv(projectId: string, file: File, baseVersionId?: string): Observable<unknown> {
    const body = new FormData(); body.append('file', file); if (baseVersionId) body.append('base_version_id', baseVersionId);
    return this.http.post(`/api/projects/${encodeURIComponent(projectId)}/csv`, body, { headers: this.headers() });
  }
  snapshotDownloadUrl(projectId: string, snapshotId: string): string {
    return `/api/projects/${encodeURIComponent(projectId)}/csv/snapshots/${encodeURIComponent(snapshotId)}/download`;
  }
  apiDatasets(projectId: string): Observable<{ connections: ApiDatasetConnection[] }> {
    return this.http.get<{ connections: ApiDatasetConnection[] }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets`);
  }
  createApiDataset(projectId: string, config: ApiDatasetConfig): Observable<{ connection: ApiDatasetConnection }> {
    return this.http.post<{ connection: ApiDatasetConnection }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets`, config, { headers: this.headers() });
  }
  updateApiDataset(projectId: string, connectionId: string, config: ApiDatasetConfig): Observable<{ connection: ApiDatasetConnection }> {
    return this.http.put<{ connection: ApiDatasetConnection }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}`, config, { headers: this.headers() });
  }
  deleteApiDataset(projectId: string, connectionId: string): Observable<{ deleted: boolean }> {
    return this.http.delete<{ deleted: boolean }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}`, { headers: this.headers() });
  }
  testApiDataset(projectId: string, connectionId: string): Observable<ApiDatasetPreview> {
    return this.http.post<ApiDatasetPreview>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}/test`, {}, { headers: this.headers() });
  }
  importApiDataset(projectId: string, connectionId: string, baseVersionId: string): Observable<{ snapshot: CsvSnapshot; version_id: string }> {
    return this.http.post<{ snapshot: CsvSnapshot; version_id: string }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}/import`, { base_version_id: baseVersionId }, { headers: this.headers() });
  }
  apiDatasetSchedule(projectId: string, connectionId: string): Observable<{ schedule: ApiDatasetSchedule | null }> {
    return this.http.get<{ schedule: ApiDatasetSchedule | null }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}/schedule`);
  }
  saveApiDatasetSchedule(projectId: string, connectionId: string, config: ApiDatasetScheduleConfig): Observable<{ schedule: ApiDatasetSchedule }> {
    return this.http.put<{ schedule: ApiDatasetSchedule }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}/schedule`, config, { headers: this.headers() });
  }
  apiDatasetRuns(projectId: string, connectionId: string, limit = 20): Observable<{ runs: ApiDatasetRun[] }> {
    const params = new HttpParams().set('limit', limit);
    return this.http.get<{ runs: ApiDatasetRun[] }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}/runs`, { params });
  }
  runApiDatasetNow(projectId: string, connectionId: string): Observable<{ run: ApiDatasetRun }> {
    return this.http.post<{ run: ApiDatasetRun }>(`/api/projects/${encodeURIComponent(projectId)}/api-datasets/${encodeURIComponent(connectionId)}/run-now`, {}, { headers: this.headers() });
  }
  records(projectId: string): Observable<{ records: SavedRecord[] }> { return this.http.get<{ records: SavedRecord[] }>(`/api/projects/${encodeURIComponent(projectId)}/records`); }
  createRecord(projectId: string, data: Record<string,unknown>): Observable<{record:SavedRecord}> {
    return this.http.post<{record:SavedRecord}>(`/api/projects/${encodeURIComponent(projectId)}/records`, { data }, { headers: this.headers() });
  }
  updateRecord(projectId: string, recordId: string, data: Record<string,unknown>, revision: number): Observable<{record:SavedRecord}> {
    return this.http.put<{record:SavedRecord}>(`/api/projects/${encodeURIComponent(projectId)}/records/${encodeURIComponent(recordId)}`, { data, expected_revision: revision }, { headers: this.headers() });
  }
  exportUrl(projectId: string, format: 'json' | 'csv'): string {
    return `/api/projects/${encodeURIComponent(projectId)}/records/export?${new HttpParams().set('format', format).toString()}`;
  }
  sources(projectId: string): Observable<{sources:ApprovedSource[]}> { return this.http.get<{sources:ApprovedSource[]}>(`/api/projects/${encodeURIComponent(projectId)}/sources`); }
  availableProjectSources(projectId: string): Observable<{sources:ApprovedSource[]}> { return this.http.get<{sources:ApprovedSource[]}>(`/api/projects/${encodeURIComponent(projectId)}/available-sources`); }
  availableSources(): Observable<{sources:ApprovedSource[]}> { return this.http.get<{sources:ApprovedSource[]}>('/api/admin/data-sources'); }
  grantSource(projectId: string, sourceId: string): Observable<unknown> { return this.http.put(`/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`,{}, {headers:this.headers()}); }
  revokeSource(projectId: string, sourceId: string): Observable<unknown> { return this.http.delete(`/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`, {headers:this.headers()}); }
  configureSource(data: {source_key:string;display_name:string;environment:string;host:string;port?:number;user_env:string;password_env:string;approved_catalogs:string[]}): Observable<{source:ApprovedSource}> { return this.http.post<{source:ApprovedSource}>('/api/admin/data-sources',data,{headers:this.headers()}); }
  sourceCatalogs(projectId: string, sourceId: string): Observable<{catalogs:string[]}> { return this.http.get<{catalogs:string[]}>(`/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/catalogs`); }
  sourceSchemas(projectId: string, sourceId: string, catalog: string): Observable<{schemas:string[]}> { return this.http.get<{schemas:string[]}>(`/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/schemas`,{params:{catalog}}); }
  sourceTables(projectId: string, sourceId: string, catalog: string, schema: string): Observable<{tables:string[]}> { return this.http.get<{tables:string[]}>(`/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/tables`,{params:{catalog,schema}}); }
  sourceRows(projectId: string, sourceId: string, catalog: string, schema: string, table: string, limit=20): Observable<{columns:string[];rows:unknown[]}> { return this.http.post<{columns:string[];rows:unknown[]}>(`/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/rows`,{catalog,schema,table,limit},{headers:this.headers()}); }
  csrfToken(): string | null { return this.csrf(); }
}

export function errorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    const message = error.error?.error?.message;
    if (typeof message === 'string' && message) return message;
    if (error.status === 0) return 'The service is unavailable. Check your connection and try again.';
  }
  return 'Something went wrong. Please try again.';
}

export function schedulerErrorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 503) return 'The scheduler is not ready. Check database readiness and confirm the schedule migration has been applied.';
    const message = error.error?.error?.message;
    if (typeof message === 'string' && message) return message;
    if (error.status === 404) return 'Scheduler API is not available yet. Restart the updated backend after the schedule migration.';
  }
  return errorMessage(error);
}
