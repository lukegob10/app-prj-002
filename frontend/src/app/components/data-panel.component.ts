import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges } from '@angular/core';
import { RouterLink } from '@angular/router';
import { catchError, forkJoin, map, of } from 'rxjs';
import { ApiDatasetConnection, ApiDatasetRun, ApiDatasetSchedule, ApiService, ApprovedSource, CsvSnapshot, CsvSnapshotPreview, Project, ProjectVersion, SavedRecord, errorMessage, schedulerErrorMessage } from '../services/api.service';

interface ApiRefreshStatus {
  connection: ApiDatasetConnection;
  schedule: ApiDatasetSchedule | null;
  lastRun: ApiDatasetRun | null;
  error: string;
}

@Component({
  selector:'agora-data-panel', standalone:true, imports:[CommonModule, RouterLink],
  template:`
    <div class="data-panel">
      <header><div><h2>Data status</h2><p>See what the live dashboard can read and when its API data refreshes.</p></div><button class="button secondary" type="button" (click)="refreshAll()">Refresh</button></header>
      <p *ngIf="loading" class="hint" role="status">Loading data…</p><p *ngIf="error" class="error" role="alert">{{error}}</p><p *ngIf="localError" class="error" role="alert">{{localError}}</p><p *ngIf="notice" class="success" role="status">{{notice}}</p>
      <div class="data-status-grid">
        <section class="status-card live-data-card" aria-labelledby="live-data-title">
          <div class="status-card-heading"><h3 id="live-data-title">Live dashboard data</h3><span class="status-chip" [class.has-data]="!!liveSnapshot">{{loading ? 'Loading data' : (liveVersion ? (liveSnapshot ? 'CSV connected' : 'No CSV snapshot') : 'Not published')}}</span></div>
          <ng-container *ngIf="liveVersion as live; else noLiveVersion">
            <div *ngIf="liveSnapshot as snapshot" class="live-snapshot"><strong>{{snapshot.filename || 'CSV snapshot'}}</strong><span>{{snapshot.row_count ?? 0}} rows · {{snapshot.columns.length}} columns</span></div>
            <p *ngIf="!loading && !liveSnapshot" class="data-warning">This live version has no CSV snapshot; dashboards using Agora.csv() will not show CSV data.</p>
            <small>Version {{live.id.slice(0,8)}} · published dashboard</small>
          </ng-container>
          <ng-template #noLiveVersion><p class="hint">No published dashboard version yet.</p></ng-template>
          <p *ngIf="sources.length" class="approved-count">{{sources.length}} approved live data source{{sources.length === 1 ? '' : 's'}}</p>
        </section>

        <section class="status-card refresh-card" aria-labelledby="refresh-status-title">
          <div class="status-card-heading"><h3 id="refresh-status-title">API refresh</h3><button type="button" class="text-button" (click)="loadApiRefreshStatus()" [disabled]="apiRefreshLoading">Refresh status</button></div>
          <p *ngIf="apiRefreshLoading && !apiRefreshStatuses.length" class="hint" role="status">Loading API refresh status…</p>
          <p *ngIf="apiRefreshError" class="error" role="alert">{{apiRefreshError}}</p>
          <div *ngIf="apiRefreshStatuses.length" class="refresh-list">
            <article *ngFor="let status of apiRefreshStatuses" class="refresh-row">
              <div class="refresh-copy"><div class="connection-status-line"><strong>{{status.connection.name}}</strong><span class="status-chip" [class.enabled]="status.schedule?.enabled">{{status.schedule ? (status.schedule.enabled ? 'Enabled' : 'Paused') : 'No schedule'}}</span></div>
                <small *ngIf="status.schedule">{{scheduleDescription(status.schedule)}}</small>
                <small *ngIf="status.schedule?.last_run_at">Last refresh {{formatScheduleDate(status.schedule!.last_run_at!,status.schedule!.timezone)}}</small>
                <small *ngIf="status.schedule?.next_run_at">Next refresh {{formatScheduleDate(status.schedule!.next_run_at!,status.schedule!.timezone)}}</small>
                <small *ngIf="status.lastRun">Latest run: {{runStatusLabel(status.lastRun.status)}}<ng-container *ngIf="status.lastRun.error_message"> · {{status.lastRun.error_message}}</ng-container></small>
                <small *ngIf="status.schedule && status.schedule.publish_mode === 'auto_publish' && !status.schedule.enabled">Re-enable this auto-publishing schedule before running it.</small>
                <small *ngIf="status.schedule && status.schedule.publish_mode === 'auto_publish' && status.schedule.enabled && !isOwner">Only a project owner can run this auto-publishing schedule.</small>
                <small *ngIf="status.error" class="error">{{status.error}}</small>
              </div>
              <button *ngIf="canWrite && status.schedule" type="button" class="button secondary small" (click)="runApiConnectionNow(status)" [disabled]="runNowBusyId === status.connection.id || isActiveRun(status.lastRun) || (status.schedule.publish_mode === 'auto_publish' && (!status.schedule.enabled || !isOwner))">{{runNowBusyId === status.connection.id ? 'Queuing…' : (isActiveRun(status.lastRun) ? 'Run in progress' : 'Run now')}}</button>
            </article>
          </div>
          <p *ngIf="!apiRefreshLoading && !apiRefreshError && !apiRefreshStatuses.length" class="hint">No API connection yet. Add one in the connection settings below.</p>
          <p *ngIf="apiRefreshPollingPaused" class="hint" role="status">A run is still in progress. Refresh status to check again.</p>
        </section>

        <section *ngIf="pendingPublishVersion as ready" class="status-card ready-version-card" aria-labelledby="ready-version-title">
          <div><h3 id="ready-version-title">Imported data is ready to review</h3><p>Version {{ready.id.slice(0,8)}} has a CSV snapshot and is not live yet.</p></div>
          <a *ngIf="isOwner" class="button primary small" [routerLink]="['/projects',project.id]" [queryParams]="{version:ready.id}">Preview and publish</a>
          <a *ngIf="!isOwner" class="button secondary small" [routerLink]="['/projects',project.id]" [queryParams]="{version:ready.id}">Preview version</a>
        </section>
      </div>

      <details class="data-history">
        <summary>CSV snapshot history and preview ({{snapshots.length}})</summary>
      <section aria-labelledby="csv-title">
        <h3 id="csv-title">CSV snapshots</h3>
        <p class="hint">Snapshots stay attached to their dashboard versions. Dashboard edits saved through Agora records appear below; they do not change the CSV file.</p>
        <div *ngIf="selectedSnapshot as snapshot" class="snapshot-current">
          <div class="snapshot-copy"><strong>{{snapshot.filename || 'CSV snapshot'}}</strong><small>{{snapshot.created_at | date:'medium'}} · {{snapshot.row_count || 0}} rows · {{snapshot.columns.length}} columns</small></div>
          <div class="snapshot-actions"><button class="button secondary" type="button" (click)="togglePreview()" [attr.aria-expanded]="previewOpen">{{previewOpen ? 'Hide data' : 'View data'}}</button><a [href]="api.snapshotDownloadUrl(project.id,snapshot.id)" download class="text-link">Download</a></div>
        </div>
        <p *ngIf="!loading && !error && !snapshots.length" class="empty">No CSV snapshots yet.</p>
        <details *ngIf="snapshots.length > 1" class="snapshot-history" #history>
          <summary>Browse all {{snapshots.length}} snapshots</summary>
          <div class="snapshot-history-list">
            <div class="snapshot-choice" *ngFor="let snapshot of snapshots">
              <button type="button" [class.selected]="selectedSnapshotId === snapshot.id" (click)="selectSnapshot(snapshot); history.open = false"><strong>{{snapshot.filename || 'CSV snapshot'}}</strong><small>{{snapshot.created_at | date:'medium'}} · {{snapshot.row_count || 0}} rows</small></button>
              <a [href]="api.snapshotDownloadUrl(project.id,snapshot.id)" download class="text-link">Download</a>
            </div>
          </div>
        </details>
        <div *ngIf="previewOpen" class="preview">
          <p *ngIf="previewLoading" class="hint" role="status">Loading page…</p><p *ngIf="previewError" class="error" role="alert">{{previewError}}</p>
          <ng-container *ngIf="preview as data">
            <div class="pager"><span>Page {{data.page}} of {{data.total_pages}} · {{data.row_count}} rows</span><div class="pager-actions"><button type="button" [disabled]="data.page <= 1" (click)="loadPage(1)">First</button><button type="button" [disabled]="data.page <= 1" (click)="loadPage(data.page - 1)">Previous</button><label>Page <input type="number" min="1" [max]="data.total_pages" [value]="pageDraft" (input)="pageDraft=asText($event)"></label><button type="button" (click)="goToPage()">Go</button><button type="button" [disabled]="data.page >= data.total_pages" (click)="loadPage(data.page + 1)">Next</button><button type="button" [disabled]="data.page >= data.total_pages" (click)="loadPage(data.total_pages)">Last</button><label>Rows <select [value]="pageSize" (change)="changePageSize($event)"><option value="10">10</option><option value="25">25</option><option value="50">50</option></select></label></div></div>
            <div class="preview-scroll"><table><thead><tr><th *ngFor="let column of data.columns" scope="col">{{column}}</th></tr></thead><tbody><tr *ngFor="let row of data.rows"><td *ngFor="let cell of row">{{cell}}</td></tr></tbody></table></div>
            <p *ngIf="!data.rows.length" class="empty">{{data.row_count ? 'No rows on this page.' : 'This CSV has headers but no data rows.'}}</p>
          </ng-container>
        </div>
      </section>
      </details>
      <details class="more-data"><summary>Saved records ({{records.length}}) and approved sources</summary>
      <section aria-labelledby="records-title"><div class="section-title"><div><h3 id="records-title">Saved records</h3><p class="hint">Shared data saved by this dashboard.</p></div><div class="downloads"><a [href]="api.exportUrl(project.id,'csv')" download>CSV</a><a [href]="api.exportUrl(project.id,'json')" download>JSON</a></div></div>
        <div *ngIf="records.length;else noRecords" class="stack"><div class="record" *ngFor="let record of records"><div><strong>{{record.id.slice(0,8)}}</strong><small>Revision {{record.revision}} · {{record.updated_at | date:'medium'}}</small></div><pre>{{record.data | json}}</pre><button *ngIf="canWrite" type="button" class="text-button" (click)="beginEdit(record)">Edit</button></div></div>
        <ng-template #noRecords><p class="empty">No records have been saved.</p></ng-template>
        <div *ngIf="canWrite" class="record-form"><h4>{{editing ? 'Edit record' : 'Add record'}}</h4><p class="hint">Enter a small JSON object.</p><label for="record-json" class="sr-only">Record JSON</label><textarea id="record-json" rows="4" [value]="recordDraft" (input)="recordDraft=asText($event)" spellcheck="false" placeholder='{"name":"Example"}'></textarea><div class="actions"><button *ngIf="editing" type="button" class="button ghost" (click)="cancelEdit()">Cancel</button><button type="button" class="button" (click)="submitRecord()">{{editing ? 'Save record' : 'Add record'}}</button></div></div>
      </section>
      <section aria-labelledby="sources-title"><h3 id="sources-title">Approved sources</h3><p class="hint">Dashboards can read only the sources approved for this project.</p>
        <p *ngIf="sourceLoading" class="hint">Loading sources…</p><p *ngIf="sourceError" class="error">{{sourceError}}</p>
        <div *ngIf="sources.length;else noSources" class="stack"><div class="row" *ngFor="let source of sources"><div><strong>{{source.display_name}}</strong><small>{{source.approved_catalogs.join(', ') || 'Approved catalogs'}}</small></div><button *ngIf="isOwner" type="button" class="text-button" (click)="revoke(source)">Remove</button></div></div>
        <ng-template #noSources><p *ngIf="!sourceLoading" class="empty">No live sources approved for this project.</p></ng-template>
        <div *ngIf="isOwner && grantableSources.length" class="grant"><label for="source-select">Add an approved source</label><div class="actions"><select id="source-select" [value]="sourceToGrant" (change)="sourceToGrant=asText($event)"><option value="">Choose source</option><option *ngFor="let source of grantableSources" [value]="source.id">{{source.display_name}}</option></select><button class="button secondary" type="button" [disabled]="!sourceToGrant" (click)="grant()">Add</button></div></div>
        <details *ngIf="isAdmin" class="admin-config"><summary>Configure a source</summary><p class="hint">Use approved server credential variable names. Credentials never appear in this browser.</p><div class="grid">
          <label>Source key<input [value]="newSource.source_key" (input)="newSource.source_key=asText($event)"></label><label>Display name<input [value]="newSource.display_name" (input)="newSource.display_name=asText($event)"></label>
          <label>Environment<select [value]="newSource.environment" (change)="newSource.environment=asText($event)"><option value="DEV">DEV</option><option value="PROD">PROD</option></select></label><label>Host<input [value]="newSource.host" (input)="newSource.host=asText($event)"></label>
          <label>User variable<input [value]="newSource.user_env" (input)="newSource.user_env=asText($event)"></label><label>Password variable<input [value]="newSource.password_env" (input)="newSource.password_env=asText($event)"></label>
          <label class="wide">Approved catalogs, comma separated<input [value]="catalogDraft" (input)="catalogDraft=asText($event)"></label>
        </div><button class="button secondary" type="button" (click)="configure()">Save source</button></details>
      </section>
      </details>
    </div>
  `,
  styles:[`
    .data-panel{display:grid;gap:19px;padding:26px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}.data-panel>header{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}h2{font-size:1.03rem;margin:0 0 3px}h3{font-size:.91rem;margin:0 0 5px}h4{font-size:.8rem;margin:0 0 4px}p{margin:0}.data-panel>header p,.hint,.empty{color:var(--text-muted);font-size:.75rem;line-height:1.45}.data-panel section{min-width:0;border-top:1px solid var(--border);padding-top:18px}.stack{display:grid;gap:7px;margin-top:11px}.row,.record{border:1px solid var(--border);background:var(--surface-raised);border-radius:8px;padding:9px 10px}.row{display:flex;align-items:center;justify-content:space-between;gap:8px}.row.selected{border-color:var(--accent)}.row strong,.record strong{font-size:.76rem}.row small,.record small{display:block;font-size:.67rem;margin-top:3px}.snapshot-actions{display:flex;align-items:center;gap:12px;flex:0 0 auto}.text-link,.text-button,.downloads a{color:var(--accent-text);font-weight:700;font-size:.72rem}.text-button{border:0;background:transparent;padding:4px;cursor:pointer}.record pre{white-space:pre-wrap;word-break:break-word;color:var(--text-muted);font-size:.68rem;max-height:115px;overflow:auto;margin:8px 0 0}.section-title{display:flex;justify-content:space-between;gap:8px}.downloads{display:flex;gap:8px}.record-form,.grant{margin-top:13px}.record-form textarea{width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-strong);border-radius:7px;padding:8px;font-size:.73rem}.actions{display:flex;gap:7px;justify-content:flex-end;margin-top:7px}.actions select{min-width:0;flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-strong);border-radius:7px;padding:7px}.success{color:var(--success-text);background:var(--success-bg);padding:8px;border-radius:7px;font-size:.76rem}.grant label,.grid label{display:grid;gap:4px;color:var(--text-muted);font-size:.73rem}.admin-config{margin-top:13px;border-top:1px solid var(--border);padding-top:12px}.admin-config summary{cursor:pointer;font-size:.77rem;color:var(--accent-text);font-weight:700}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0}.grid .wide{grid-column:span 2}.grid input,.grid select{min-width:0;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-strong);border-radius:7px;padding:7px;font-size:.73rem}.empty{margin-top:10px}.preview{min-width:0;margin-top:14px}.preview-heading{margin-bottom:8px}.preview-scroll{max-width:100%;overflow:auto;border:1px solid var(--border);border-radius:8px}.preview table{width:100%;min-width:max-content;border-collapse:collapse;text-align:left;font-size:.72rem}.preview th,.preview td{max-width:260px;padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.preview th{position:sticky;top:0;background:var(--surface-raised)}.preview tr:last-child td{border-bottom:0}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
    .snapshot-current{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:12px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--surface-raised)}.snapshot-copy{display:grid;min-width:0;gap:4px}.snapshot-copy strong{font-size:.83rem;overflow-wrap:anywhere}.snapshot-copy small{color:var(--text-muted);font-size:.72rem}.snapshot-history{margin-top:10px}.snapshot-history summary{cursor:pointer;color:var(--accent-text);font-size:.76rem;font-weight:700}.snapshot-history-list{max-height:240px;overflow:auto;margin-top:8px;border:1px solid var(--border);border-radius:8px}.snapshot-choice{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:7px 10px;border-bottom:1px solid var(--border)}.snapshot-choice:last-child{border-bottom:0}.snapshot-choice button{display:grid;flex:1;min-width:0;gap:3px;padding:5px;border:0;border-radius:5px;background:transparent;color:var(--text);text-align:left;cursor:pointer}.snapshot-choice button:hover,.snapshot-choice button.selected{background:var(--surface-hover)}.snapshot-choice strong{font-size:.76rem;overflow-wrap:anywhere}.snapshot-choice small{color:var(--text-muted);font-size:.68rem}.pager{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:9px;color:var(--text-muted);font-size:.73rem}.pager-actions{display:flex;align-items:center;flex-wrap:wrap;gap:6px}.pager-actions button,.pager-actions input,.pager-actions select{min-height:30px;padding:4px 7px;border:1px solid var(--border-strong);border-radius:6px;background:var(--input-bg);color:var(--text);font:inherit}.pager-actions button{cursor:pointer}.pager-actions button:disabled{opacity:.45;cursor:not-allowed}.pager-actions label{display:flex;align-items:center;gap:5px}.pager-actions input{width:58px}.pager-actions select{width:65px}.preview-scroll{max-height:460px}
    .more-data{border-top:1px solid var(--border);padding-top:15px}.more-data summary{cursor:pointer;color:var(--text);font-size:.82rem;font-weight:700}.more-data section:first-of-type{margin-top:16px}
    .data-panel{gap:23px;box-shadow:0 1px 2px rgb(0 0 0 / 3%)}.data-panel>header{align-items:center}.data-panel>header h2{font-size:1.05rem;color:var(--text)}.data-panel>header p{margin-top:4px;font-size:.78rem}.data-panel>section{padding-top:20px}.data-panel section>.hint{max-width:72ch}.snapshot-current{padding:15px 17px;background:var(--bg)}.snapshot-copy strong{font-size:.85rem}.snapshot-copy small{font-size:.73rem}.snapshot-history summary,.more-data summary{padding:6px 0;line-height:1.4}.snapshot-history summary:hover,.more-data summary:hover{color:var(--accent-text)}.snapshot-history-list{background:var(--bg)}.snapshot-choice:hover{background:var(--surface-hover)}.preview{margin-top:17px}.pager{padding:10px 12px;margin-bottom:0;border:1px solid var(--border);border-bottom:0;border-radius:8px 8px 0 0;background:var(--surface-raised);color:var(--text)}.pager>span{font-weight:700}.pager-actions button{background:var(--surface);font-weight:600}.pager-actions button:hover:not(:disabled){background:var(--surface-hover);border-color:var(--accent)}.pager-actions input:focus-visible,.pager-actions select:focus-visible{outline:2px solid var(--focus);outline-offset:1px}.preview-scroll{border-radius:0 0 8px 8px;background:var(--surface)}.preview th,.preview td{padding:11px 13px;border-bottom-color:var(--border)}.preview th{z-index:1;color:var(--text);font-size:.7rem;font-weight:700}.preview tbody tr:nth-child(even){background:var(--bg)}.preview tbody tr:hover{background:var(--surface-hover)}.row,.record{background:var(--bg)}.record pre{padding:10px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text)}
    .data-status-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);gap:11px}.status-card{display:grid;align-content:start;gap:10px;min-width:0;padding:14px;border:1px solid var(--border)!important;border-radius:9px;background:var(--bg)}.status-card h3{margin:0;font-size:.83rem}.status-card small,.live-snapshot span,.approved-count{color:var(--text-muted);font-size:.7rem;line-height:1.45}.status-card-heading,.connection-status-line{display:flex;align-items:center;justify-content:space-between;gap:8px}.status-chip{display:inline-flex;flex:0 0 auto;padding:3px 7px;border-radius:999px;background:var(--surface-hover);color:var(--text-muted);font-size:.62rem;font-weight:750}.status-chip.has-data,.status-chip.enabled{background:var(--success-bg);color:var(--success-text)}.live-snapshot{display:grid;gap:3px}.live-snapshot strong,.connection-status-line strong{font-size:.77rem;overflow-wrap:anywhere}.data-warning{padding:9px 10px;border-radius:7px;background:var(--error-bg);color:var(--error-text);font-size:.72rem;line-height:1.45}.approved-count{margin:0}.refresh-list{display:grid;gap:0}.refresh-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px solid var(--border)}.refresh-copy{display:grid;min-width:0;gap:4px}.connection-status-line{justify-content:flex-start}.refresh-copy small{overflow-wrap:anywhere}.refresh-row .button.small{min-height:31px;flex:0 0 auto;padding:5px 9px;font-size:.69rem}.ready-version-card{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;gap:12px;border-color:var(--accent)!important;background:var(--accent-soft)}.ready-version-card p{color:var(--text-muted);font-size:.72rem;line-height:1.45}.data-history,.more-data{border-top:1px solid var(--border);padding-top:12px}.data-history>summary,.more-data>summary{cursor:pointer;color:var(--text);font-size:.8rem;font-weight:700;padding:5px 0}.data-history>summary:hover,.more-data>summary:hover{color:var(--accent-text)}.data-history>section{padding-top:14px;border-top:0}
    @media(max-width:760px){.data-status-grid{grid-template-columns:1fr}.ready-version-card{grid-column:auto}}
    @media(max-width:620px){.data-panel{padding:20px}.data-panel>header{align-items:flex-start}.row,.snapshot-current{align-items:flex-start;flex-direction:column}.snapshot-actions{width:100%;justify-content:flex-end}.pager{align-items:flex-start}.pager-actions{gap:5px}.preview th,.preview td{padding:9px 10px}.refresh-row{align-items:flex-start;flex-direction:column}.ready-version-card{align-items:flex-start;flex-direction:column}}
  `]
})
export class DataPanelComponent implements OnChanges, OnDestroy {
  @Input({required:true}) project!:Project; @Input() versions:ProjectVersion[]=[]; @Input() snapshots:CsvSnapshot[]=[]; @Input() records:SavedRecord[]=[]; @Input() loading=false; @Input() error='';
  @Output() refresh=new EventEmitter<void>(); @Output() createRecord=new EventEmitter<{data:Record<string,unknown>}>(); @Output() updateRecord=new EventEmitter<{id:string;data:Record<string,unknown>;revision:number}>();
  sources:ApprovedSource[]=[]; availableSources:ApprovedSource[]=[]; sourceLoading=false; sourceError=''; localError=''; notice=''; sourceToGrant=''; recordDraft=''; editing:SavedRecord|null=null; catalogDraft='';
  apiRefreshStatuses:ApiRefreshStatus[]=[]; apiRefreshLoading=false; apiRefreshError=''; runNowBusyId:string|null=null; apiRefreshPollingPaused=false;
  selectedSnapshotId:string|null=null; previewOpen=false; preview:CsvSnapshotPreview|null=null; previewLoading=false; previewError=''; pageSize=10; pageDraft='1'; private previewGeneration=0;
  newSource={source_key:'',display_name:'',environment:'DEV',host:'',user_env:'',password_env:''};
  get isAdmin():boolean{return this.project?.role==='admin' || !!this.api.currentUser()?.is_admin;} get isOwner():boolean{return this.project?.role==='owner'||this.isAdmin;} get canWrite():boolean{return this.isOwner||this.project?.role==='editor';}
  get selectedSnapshot():CsvSnapshot|null{return this.snapshots.find(snapshot=>snapshot.id===this.selectedSnapshotId)||null;}
  get liveVersion():ProjectVersion|null{return this.versions.find(version=>version.id===this.project?.published_version_id)||null;}
  get liveSnapshot():CsvSnapshot|null{return this.liveVersion?.csv_snapshot_id?this.snapshots.find(snapshot=>snapshot.id===this.liveVersion?.csv_snapshot_id)||null:null;}
  get pendingPublishVersion():ProjectVersion|null{
    const published=this.liveVersion;
    if(this.project?.published_version_id&&!published)return null;
    const publishedTime=published?Date.parse(published.created_at):Number.NEGATIVE_INFINITY;
    return this.versions
      .filter(version=>!!version.csv_snapshot_id&&version.id!==published?.id&&version.is_published!==true&&Date.parse(version.created_at)>publishedTime)
      .sort((left,right)=>Date.parse(right.created_at)-Date.parse(left.created_at))[0]||null;
  }
  get grantableSources():ApprovedSource[]{return this.availableSources.filter(source=>!this.sources.some(approved=>approved.id===source.id));}
  private apiRefreshGeneration=0; private apiRefreshPollTimer:ReturnType<typeof setTimeout>|null=null; private apiRefreshPollCount=0;
  constructor(public readonly api:ApiService){}
  ngOnChanges(changes:SimpleChanges):void{
    if(changes['project'] && this.project){this.loadSources();this.loadApiRefreshStatus();}
    if(changes['snapshots']){
      const first=this.snapshots[0];
      const previousFirst=(changes['snapshots'].previousValue as CsvSnapshot[] | undefined)?.[0];
      if(first && (first.id!==previousFirst?.id || !this.selectedSnapshot)) this.selectSnapshot(first);
      else if(!first){++this.previewGeneration;this.selectedSnapshotId=null;this.previewOpen=false;this.preview=null;this.previewLoading=false;this.previewError='';}
    }
  }
  ngOnDestroy():void{this.apiRefreshGeneration++;this.clearApiRefreshPoll();}
  refreshAll():void{this.refresh.emit();this.loadApiRefreshStatus();}
  private clearApiRefreshPoll():void{if(this.apiRefreshPollTimer!==null){clearTimeout(this.apiRefreshPollTimer);this.apiRefreshPollTimer=null;}}
  loadApiRefreshStatus(silent=false):void{
    if(!this.project)return;
    this.clearApiRefreshPoll();
    if(!silent){this.apiRefreshPollCount=0;this.apiRefreshPollingPaused=false;this.apiRefreshLoading=true;}
    this.apiRefreshError='';
    const generation=++this.apiRefreshGeneration;
    this.api.apiDatasets(this.project.id).subscribe({
      next:({connections})=>{
        if(generation!==this.apiRefreshGeneration)return;
        if(!connections.length){this.apiRefreshStatuses=[];this.apiRefreshLoading=false;this.apiRefreshPollCount=0;return;}
        const requests=connections.map(connection=>forkJoin({
          schedule:this.api.apiDatasetSchedule(this.project.id,connection.id),
          runs:this.api.apiDatasetRuns(this.project.id,connection.id,1),
        }).pipe(
          map(({schedule,runs}):ApiRefreshStatus=>({connection,schedule:schedule.schedule,lastRun:runs.runs[0]||null,error:''})),
          catchError(error=>of<ApiRefreshStatus>({connection,schedule:null,lastRun:null,error:schedulerErrorMessage(error)})),
        ));
        forkJoin(requests).subscribe({
          next:statuses=>{
            if(generation!==this.apiRefreshGeneration)return;
            this.apiRefreshLoading=false;
            if(statuses.length&&statuses.every(status=>!!status.error)){
              this.apiRefreshError=statuses[0].error;
              this.apiRefreshStatuses=[];
              return;
            }
            this.apiRefreshStatuses=statuses;
            const hasActiveRun=statuses.some(status=>this.isActiveRun(status.lastRun));
            if(hasActiveRun&&this.apiRefreshPollCount<12){
              this.apiRefreshPollCount++;
              this.apiRefreshPollTimer=setTimeout(()=>this.loadApiRefreshStatus(true),5000);
            }else if(hasActiveRun)this.apiRefreshPollingPaused=true;
            else{this.apiRefreshPollCount=0;this.apiRefreshPollingPaused=false;}
          },
          error:error=>{
            if(generation!==this.apiRefreshGeneration)return;
            this.apiRefreshLoading=false;this.apiRefreshError=schedulerErrorMessage(error);
          },
        });
      },
      error:error=>{if(generation===this.apiRefreshGeneration){this.apiRefreshLoading=false;this.apiRefreshError=schedulerErrorMessage(error);}},
    });
  }
  runApiConnectionNow(status:ApiRefreshStatus):void{
    if(!status.schedule||!this.canWrite||this.runNowBusyId||this.isActiveRun(status.lastRun)||(status.schedule.publish_mode==='auto_publish'&&(!status.schedule.enabled||!this.isOwner)))return;
    this.runNowBusyId=status.connection.id;this.apiRefreshError='';
    this.api.runApiDatasetNow(this.project.id,status.connection.id).subscribe({
      next:({run})=>{
        this.runNowBusyId=null;this.apiRefreshPollCount=0;this.apiRefreshPollingPaused=false;
        this.apiRefreshStatuses=this.apiRefreshStatuses.map(item=>item.connection.id===status.connection.id?{...item,lastRun:run}:item);
        this.notice=run.status==='unchanged'?'The API response matches the latest snapshot; no new version was created.':'Refresh queued. New data is saved according to this schedule’s publish mode.';
        this.loadApiRefreshStatus(true);
      },
      error:error=>{this.runNowBusyId=null;this.apiRefreshError=schedulerErrorMessage(error);},
    });
  }
  isActiveRun(run:ApiDatasetRun|null):boolean{return run?.status==='queued'||run?.status==='running';}
  runStatusLabel(status:ApiDatasetRun['status']):string{return status==='needs_review'?'Needs review':status==='unchanged'?'No change':status.charAt(0).toUpperCase()+status.slice(1);}
  scheduleDescription(schedule:ApiDatasetSchedule):string{
    if(schedule.frequency==='interval')return schedule.interval_minutes===60?'Hourly':'Every '+schedule.interval_minutes+' minutes';
    const time=schedule.local_time||'';
    if(schedule.frequency==='daily')return 'Daily at '+time+' '+schedule.timezone;
    if(schedule.frequency==='weekly')return 'Weekly on '+(schedule.weekdays||[]).map(day=>this.weekdayName(day)).join(', ')+' at '+time+' '+schedule.timezone;
    return 'Monthly on day '+schedule.day_of_month+' at '+time+' '+schedule.timezone;
  }
  formatScheduleDate(timestamp:string,timezone:string):string{
    const date=new Date(timestamp);
    if(Number.isNaN(date.getTime()))return timestamp;
    try{return new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short',timeZone:timezone}).format(date);}
    catch{return date.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'});}
  }
  private weekdayName(day:number):string{return ['','Mon','Tue','Wed','Thu','Fri','Sat','Sun'][day]||'';}
  selectSnapshot(snapshot:CsvSnapshot):void{
    if(this.selectedSnapshotId===snapshot.id)return;
    ++this.previewGeneration;
    this.selectedSnapshotId=snapshot.id;this.preview=null;this.previewError='';this.previewLoading=false;this.pageDraft='1';
    if(this.previewOpen)this.loadPage(1);
  }
  togglePreview():void{
    this.previewOpen=!this.previewOpen;
    if(this.previewOpen && !this.preview)this.loadPage(1);
  }
  loadPage(page:number):void{
    if(!this.selectedSnapshotId || page<1)return;
    const generation=++this.previewGeneration;
    this.preview=null;this.previewError='';this.previewLoading=true;
    this.api.previewSnapshot(this.project.id,this.selectedSnapshotId,page,this.pageSize).subscribe({next:data=>{if(generation===this.previewGeneration){this.preview=data;this.pageDraft=String(data.page);this.previewLoading=false;}},error:e=>{if(generation===this.previewGeneration){this.previewError=errorMessage(e);this.previewLoading=false;}}});
  }
  goToPage():void{
    if(!this.preview)return;
    const page=Number(this.pageDraft);
    if(!Number.isSafeInteger(page)||page<1||page>this.preview.total_pages){this.previewError=`Choose a page from 1 to ${this.preview.total_pages}.`;return;}
    this.loadPage(page);
  }
  changePageSize(event:Event):void{
    const size=Number(this.asText(event));
    if(![10,25,50].includes(size))return;
    this.pageSize=size;this.loadPage(1);
  }
  asText(event:Event):string{return (event.target as HTMLInputElement).value;}
  loadSources():void{this.sourceLoading=true;this.sourceError='';this.api.sources(this.project.id).subscribe({next:d=>{this.sources=d.sources;this.sourceLoading=false;},error:e=>{this.sourceLoading=false;this.sourceError=errorMessage(e);}});if(this.isOwner)this.api.availableProjectSources(this.project.id).subscribe({next:d=>this.availableSources=d.sources,error:e=>this.sourceError=errorMessage(e)});}
  beginEdit(record:SavedRecord):void{this.editing=record;this.recordDraft=JSON.stringify(record.data,null,2);}
  cancelEdit():void{this.editing=null;this.recordDraft='';}
  submitRecord():void{let data:unknown;try{data=JSON.parse(this.recordDraft);}catch{this.localError='Enter valid JSON.';return;}if(!data||typeof data!=='object'||Array.isArray(data)){this.localError='Enter a JSON object.';return;}this.localError='';if(this.editing)this.updateRecord.emit({id:this.editing.id,data:data as Record<string,unknown>,revision:this.editing.revision});else this.createRecord.emit({data:data as Record<string,unknown>});this.cancelEdit();}
  grant():void{if(!this.sourceToGrant)return;this.api.grantSource(this.project.id,this.sourceToGrant).subscribe({next:()=>{this.notice='Source added.';this.sourceToGrant='';this.loadSources();},error:e=>this.sourceError=errorMessage(e)});}
  revoke(source:ApprovedSource):void{this.api.revokeSource(this.project.id,source.id).subscribe({next:()=>{this.notice='Source removed.';this.loadSources();},error:e=>this.sourceError=errorMessage(e)});}
  configure():void{const values={...this.newSource,approved_catalogs:this.catalogDraft.split(',').map(x=>x.trim()).filter(Boolean)};this.api.configureSource(values).subscribe({next:()=>{this.notice='Source saved.';this.loadSources();},error:e=>this.sourceError=errorMessage(e)});}
}
