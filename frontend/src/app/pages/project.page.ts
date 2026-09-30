import { Component, ElementRef, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpEventType } from '@angular/common/http';
import { Subscription, timeout } from 'rxjs';
import { ApiService, CsvSnapshot, Member, Project, ProjectVersion, SavedRecord, errorMessage } from '../services/api.service';
import { ProjectControlsComponent } from '../components/project-controls.component';
import { DataPanelComponent } from '../components/data-panel.component';
import { BrandComponent } from '../components/brand.component';
import { ThemeToggleComponent } from '../components/theme-toggle.component';
import { projectPath, spacePath } from '../project-paths';

@Component({
  selector: 'agora-project-page', standalone: true,
  imports: [CommonModule, RouterLink, ProjectControlsComponent, DataPanelComponent, BrandComponent, ThemeToggleComponent],
  template: `
    <div class="project-shell" [class.dashboard-open]="!managing">
      <header class="topbar">
        <agora-brand [compactOnMobile]="true" />
        <span class="divider"></span>
        <span class="project-name">{{ project?.name || 'Project' }}</span>
        <span *ngIf="project" class="role">{{ project.role }}</span>
        <div class="top-spacer"></div>
        <agora-theme-toggle />
        <a [routerLink]="spacePath(api.currentUser()?.username || '')" class="back">My Space</a>
      </header>
      <div class="loading" *ngIf="loading">Opening project…</div>
      <div class="fatal" *ngIf="error && !project"><p class="error">{{ error }}</p><button class="button secondary" (click)="reload()">Try again</button></div>
      <ng-container *ngIf="project as p">
        <main *ngIf="managing; else dashboard" class="management">
          <header class="project-heading">
            <div><p class="eyebrow">Project workspace</p><h1>{{ p.name }}</h1><p class="description">{{ p.description || 'Upload your dashboard, manage access, and keep your project data together.' }}</p></div>
            <div class="heading-actions" *ngIf="p.published_version_id">
              <button class="button ghost heading-action" type="button" (click)="copyDashboardLink()">Copy dashboard link</button>
              <button class="button ghost heading-action" type="button" (click)="showLive()">Open dashboard <span aria-hidden="true">↗</span></button>
            </div>
          </header>
          <p *ngIf="notice" class="notice" role="status">{{ notice }}</p>
          <p *ngIf="error" class="error" role="alert">{{ error }}</p>
          <nav class="project-tabs" aria-label="Project tools">
            <a [routerLink]="[]" [queryParams]="{view: 'manage', tab: 'versions'}" [attr.aria-current]="tab === 'versions' ? 'page' : null" [class.active]="tab === 'versions'">Versions</a>
            <a *ngIf="canEditAccess" [routerLink]="[]" [queryParams]="{view: 'manage', tab: 'access'}" [attr.aria-current]="tab === 'access' ? 'page' : null" [class.active]="tab === 'access'">Access & settings</a>
            <a [routerLink]="[]" [queryParams]="{view: 'manage', tab: 'data'}" [attr.aria-current]="tab === 'data' ? 'page' : null" [class.active]="tab === 'data'">Data</a>
          </nav>
          <agora-data-panel *ngIf="tab === 'data'" [project]="p" [versions]="versions" [snapshots]="snapshots" [records]="records" [loading]="dataLoading" [error]="dataError" (refresh)="loadData()" (createRecord)="createRecord($event)" (updateRecord)="updateRecord($event)" />
          <app-project-controls [class.data-controls]="tab === 'data'" [section]="tab" [project]="p" [versions]="versions" [members]="members" [loading]="busy" [memberLoading]="memberLoading" [previousVersionId]="previousVersionId"
            (upload)="upload($event)" (publish)="publish($event)" (rollback)="rollback()" (refresh)="reloadDetails()"
            (updateProject)="updateProject($event)" (addMember)="addMember($event)" (removeMember)="removeMember($event)" (replaceCsv)="replaceCsv($event)" (importApiDataset)="importApiDataset($event)" />
        </main>
        <ng-template #dashboard>
          <main class="viewer-area">
            <div class="viewer-toolbar">
              <div class="version-state">
                <span class="badge" [class.live]="!preview && !!p.published_version_id">{{ preview ? 'Preview' : (p.published_version_id ? 'Live' : 'Unpublished') }}</span>
                <span class="version-label" *ngIf="selectedVersion">{{ preview ? 'Working version' : 'Published version' }} · {{ (preview ? selectedVersion.created_at : (publishedAt || selectedVersion.created_at)) | date:'medium' }}</span>
              </div>
              <div class="toolbar-actions">
                <button *ngIf="p.published_version_id" class="button ghost" type="button" (click)="copyDashboardLink()">Copy link</button>
                <button *ngIf="preview && p.published_version_id" class="button ghost" type="button" (click)="showLive()">View live</button>
                <button *ngIf="canPublishPreview" class="button primary" type="button" (click)="publishPreview()" [disabled]="busy">Publish this version</button>
                <button *ngIf="canManage" class="button ghost manage-action" type="button" (click)="showManagement()">{{ preview ? 'Back to project' : 'Manage project' }}</button>
              </div>
            </div>
            <p *ngIf="notice" class="notice" role="status">{{ notice }}</p>
            <p *ngIf="viewerWarning" class="viewer-warning" role="status">{{ viewerWarning }}</p>
            <p *ngIf="error" class="error viewer-error" role="alert">{{ error }}</p>
            <div #viewerMount class="frame-wrap" *ngIf="frameSrc; else noContent"></div>
            <ng-template #noContent><div class="no-content"><div class="empty-illustration">▣</div><h1>{{ selectedVersion ? 'Opening dashboard…' : 'No dashboard to show yet' }}</h1><p *ngIf="!selectedVersion">{{ canManage ? 'Return to your project to upload and publish a dashboard.' : 'The owner has not published a dashboard yet.' }}</p></div></ng-template>
          </main>
        </ng-template>
      </ng-container>
    </div>
  `,
  styles: [`
    :host{display:block;min-height:100vh;background:var(--bg);color:var(--text)}.project-shell{min-height:100vh;display:flex;flex-direction:column}.project-shell.dashboard-open{height:100dvh}
    .topbar{height:62px;min-height:62px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:14px;padding:0 clamp(18px,3vw,40px)}
    .divider{height:19px;border-left:1px solid var(--border)}.project-name{font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.role{text-transform:capitalize;color:var(--text-muted);font-size:.72rem;border:1px solid var(--border);border-radius:20px;padding:3px 9px}.top-spacer{flex:1}.back{font-size:.82rem;font-weight:600;color:var(--text-muted);white-space:nowrap}.back:hover{color:var(--text);text-decoration:none}
    .loading,.fatal{margin:40px auto}.fatal{max-width:420px}.management{width:min(1080px,100%);margin:0 auto;padding:36px clamp(22px,4vw,48px) 56px}.project-heading{display:flex;justify-content:space-between;align-items:center;gap:24px;margin-bottom:30px}.project-heading>div{min-width:0}.project-heading h1{margin:0 0 8px;overflow-wrap:anywhere;font-size:clamp(1.55rem,2.2vw,1.9rem);letter-spacing:-.04em}.project-heading button{flex-shrink:0}.eyebrow{color:var(--text-subtle);font-size:.68rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px}.description{color:var(--text-muted);line-height:1.6;margin:0;max-width:650px;font-size:.91rem}.heading-action{font-size:.78rem;padding:8px 11px}.heading-action span{margin-left:3px;color:var(--text-subtle)}.project-tabs{display:flex;align-items:center;gap:6px;overflow-x:auto;border-bottom:1px solid var(--border);margin:0 0 22px;padding:0 0 0;scrollbar-width:thin}.project-tabs a{flex:0 0 auto;padding:10px 13px 11px;border-bottom:2px solid transparent;border-radius:7px 7px 0 0;color:var(--text-muted);font-weight:650;font-size:.84rem;white-space:nowrap;transition:background .15s ease,color .15s ease,border-color .15s ease}.project-tabs a:hover{text-decoration:none;color:var(--text);background:var(--surface-hover)}.project-tabs a.active{color:var(--accent-text);border-bottom-color:var(--accent);background:var(--accent-soft)}agora-data-panel{display:block}.data-controls{display:block;margin-top:22px}
    .viewer-area{display:flex;flex-direction:column;min-width:0;min-height:0;flex:1;background:var(--bg)}.viewer-toolbar{min-height:56px;display:flex;align-items:center;justify-content:space-between;padding:9px clamp(14px,2.5vw,30px);background:var(--surface);border-bottom:1px solid var(--border);gap:12px}.version-state,.toolbar-actions{display:flex;align-items:center;gap:10px}.version-label{font-size:.78rem;color:var(--text-muted)}.toolbar-actions button{font-size:.76rem}.button.manage-action{min-height:33px;padding:6px 10px;border-color:transparent;color:var(--text-muted)}.button.manage-action:hover{border-color:var(--border);color:var(--text);background:var(--surface-raised)}.notice{margin:0 0 16px;padding:10px 16px;border-radius:8px;color:var(--success-text);background:var(--success-bg);font-size:.85rem}.viewer-area .notice{margin:0;border-radius:0}.viewer-warning{margin:0;padding:9px 18px;color:var(--text);background:var(--accent-soft);font-size:.79rem}.viewer-error{margin:10px 18px}.frame-wrap{flex:1;min-height:0;background:var(--surface)}.no-content{margin:auto;text-align:center;max-width:420px;padding:25px}.no-content p{color:var(--text-muted);line-height:1.5}.empty-illustration{font-size:2.3rem;color:var(--text-subtle);margin-bottom:12px}
    .heading-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
    @media(max-width:760px){.management{padding-top:30px}.project-heading{align-items:flex-start}.description{max-width:560px}}
    @media(max-width:620px){.topbar{padding:0 14px;gap:10px}.role,.version-label{display:none}.management{padding:26px 18px 38px}.project-heading{flex-direction:column;gap:14px;margin-bottom:24px}.project-heading button{align-self:flex-start}.project-tabs{gap:3px;margin-bottom:18px}.project-tabs a{padding:9px 10px 10px;font-size:.78rem}.viewer-toolbar{padding:9px 12px}.toolbar-actions{gap:5px}.toolbar-actions button{padding:.52rem .62rem}}
  `]
})
export class ProjectPage implements OnInit, OnDestroy {
  readonly spacePath = spacePath;
  project: Project | null = null; versions: ProjectVersion[] = []; members: Member[] = [];
  snapshots: CsvSnapshot[] = []; records: SavedRecord[] = [];
  selectedVersion: ProjectVersion | null = null; frameSrc: string | null = null; publishedAt: string | null = null; previousVersionId: string | null = null;
  preview = false; loading = true; busy = false; memberLoading = false; dataLoading = false;
  managing = true; tab: 'versions' | 'access' | 'data' = 'versions'; error = ''; dataError = ''; notice = ''; viewerWarning = '';
  private projectId = ''; private authenticated = false; private subscriptions = new Subscription(); private bridge: AgoraBridge | null = null; private grantGeneration = 0; private approvedSourceCount = 0;
  private frameMount: HTMLDivElement | null = null;
  get canManage(): boolean { return !!this.project && ['owner','editor','admin'].includes(this.project.role); }
  get canEditAccess(): boolean { return !!this.project && ['owner', 'admin'].includes(this.project.role); }
  get canPublishPreview(): boolean {
    return !!this.preview && !!this.selectedVersion && !!this.project
      && this.selectedVersion.id !== this.project.published_version_id
      && ['owner', 'admin'].includes(this.project.role);
  }
  constructor(public readonly api: ApiService, private readonly route: ActivatedRoute, private readonly router: Router) {}
  ngOnInit(): void {
    this.subscriptions.add(this.route.paramMap.subscribe(params => {
      const projectId = params.get('id') || '';
      if (this.projectId !== projectId) this.project = null;
      this.projectId = projectId;
      if (this.authenticated) this.reload();
    }));
    this.subscriptions.add(this.route.queryParamMap.subscribe(() => this.chooseVersion()));
    this.subscriptions.add(this.api.auth().subscribe({next: auth => {
      if (!auth) {
        void this.router.navigate(['/login'], { queryParams: { returnUrl: this.router.url } });
        return;
      }
      this.authenticated = true;
      this.reload();
    }, error: e => {this.loading=false;this.error=errorMessage(e);} }));
  }
  ngOnDestroy(): void { this.subscriptions.unsubscribe(); ++this.grantGeneration; this.bridge?.destroy(); }
  reload(afterReload?: () => void): void {
    this.loading = true; this.error = '';
    this.api.getProject(this.projectId).subscribe({next: p => {
      if (this.router.url.split(/[?#]/, 1)[0] !== projectPath(p.owner_username, p.id)) {
        void this.router.navigateByUrl(
          this.router.createUrlTree([projectPath(p.owner_username, p.id)], {
            queryParams: this.route.snapshot.queryParams,
            fragment: this.route.snapshot.fragment ?? undefined,
          }),
          { replaceUrl: true },
        );
        return;
      }
      this.project = p; this.loading = false;
      this.api.sources(this.projectId).pipe(timeout(5000)).subscribe({next:d=>{this.approvedSourceCount=d.sources.length;this.reloadDetails();afterReload?.();},error:()=>{this.approvedSourceCount=0;this.reloadDetails();afterReload?.();}});
    }, error: e => {this.loading=false;this.error=errorMessage(e);} });
  }
  reloadDetails(): void {
    this.api.published(this.projectId).subscribe({next:data=>{this.publishedAt=data.publication?.published_at||null;this.previousVersionId=data.publication?.previous_version_id||null;if(!this.canManage){this.versions=data.version?[data.version]:[];this.chooseVersion();}},error:e=>this.error=errorMessage(e)});
    if (this.canManage) this.api.versions(this.projectId).subscribe({ next: data => { this.versions = data.versions; this.chooseVersion(); }, error: e => this.error = errorMessage(e) });
    if (this.canEditAccess) { this.memberLoading = true; this.api.members(this.projectId).subscribe({next: data => {this.members=data.members;this.memberLoading=false;}, error: e => {this.memberLoading=false;this.error=errorMessage(e);} }); }
  }
  private chooseVersion(): void {
    if (!this.project) return;
    const params = this.route.snapshot.queryParamMap;
    const requested = params.get('version');
    const nextTab = params.get('tab');
    const wasData = this.managing && this.tab === 'data';
    this.tab = nextTab === 'data' ? 'data' : nextTab === 'access' && this.canEditAccess ? 'access' : 'versions';
    this.managing = this.canManage && (params.get('view') === 'manage' || (!this.project.published_version_id && !requested));
    if (this.managing && this.tab === 'data' && !wasData) this.loadData();
    const versionId = requested && this.canManage ? requested : this.project.published_version_id;
    this.preview = !!requested && this.canManage && requested !== this.project.published_version_id;
    this.selectedVersion = this.versions.find(v => v.id === versionId) || null;
    this.bridge?.destroy(); this.bridge=null; this.frameMount?.replaceChildren(); this.frameSrc=null; this.frameGrant=null; this.viewerWarning='';
    const generation=++this.grantGeneration;
    if (!this.managing && versionId && this.selectedVersion) this.api.viewGrant(this.projectId, versionId).subscribe({next: grant => {
      if (generation === this.grantGeneration) {
        this.frameGrant=grant.grant_token;this.frameSrc=grant.url;
        if (this.frameMount?.isConnected) this.attachBridge(this.frameMount);
      }
    },error:e=>{if(generation===this.grantGeneration)this.error=errorMessage(e);}});
  }
  showLive(): void { this.router.navigate([], { relativeTo: this.route, queryParams: {} }); }
  showManagement(): void { this.router.navigate([], { relativeTo: this.route, queryParams: {view: 'manage', tab: 'versions'} }); }
  async copyDashboardLink(): Promise<void> {
    if (!this.project?.published_version_id) return;
    const url = new URL(projectPath(this.project.owner_username, this.project.id), window.location.origin).href;
    try {
      await navigator.clipboard.writeText(url);
      this.notice = 'Dashboard link copied.';
    } catch {
      this.notice = `Copy this dashboard link: ${url}`;
    }
  }
  frameGrant: string | null = null;
  @ViewChild('viewerMount') set viewerMount(ref: ElementRef<HTMLDivElement> | undefined) {
    this.frameMount = ref?.nativeElement || null;
    if (ref && this.frameSrc && this.frameGrant && this.selectedVersion) queueMicrotask(() => this.attachBridge(ref.nativeElement));
  }
  private attachBridge(mount: HTMLDivElement): void {
    if (!mount.isConnected || this.managing || !this.frameSrc || !this.frameGrant || !this.selectedVersion || mount.childElementCount) return;
    if (!window.AgoraHost) {this.error='The dashboard viewer is unavailable. Reload this page to try again.';return;}
    const iframe=document.createElement('iframe');
    iframe.title=`${this.project?.name || 'Project'} dashboard`;
    iframe.style.cssText='border:0;width:100%;height:100%;display:block';
    const capabilities=['records.read'];
    if (this.selectedVersion.csv_snapshot_id) capabilities.push('csv.read');
    if (this.approvedSourceCount) capabilities.push('sources.read');
    if (this.project && (this.project.allow_viewer_writes || this.project.role !== 'viewer')) capabilities.push('records.write');
    try {
      const hasData = !!this.selectedVersion.csv_snapshot_id || this.approvedSourceCount > 0 || !!this.project?.allow_viewer_writes;
      this.bridge=window.AgoraHost.createAgoraViewerBridge({iframe,src:this.frameSrc,projectId:this.projectId,versionId:this.selectedVersion.id,grantToken:this.frameGrant,capabilities,getCsrfToken:()=>this.api.csrfToken(),onError:error=>{if(error.code==='HANDSHAKE_TIMEOUT' && !hasData)return;this.viewerWarning='This dashboard’s data connection is unavailable. Try reopening the project.';}});
      mount.appendChild(iframe);
    } catch {this.error='The dashboard viewer could not start. Reload this page to try again.';}
  }
  private changed(message: string, openDashboard = false): void {
    this.busy = false; this.notice = message; this.error = '';
    this.reload(() => {
      if (openDashboard) this.showLive();
      else if (this.managing && this.tab === 'data') this.loadData();
    });
  }
  upload(payload: { file: File; csv: File | null }): void {
    this.busy = true; this.notice = 'Uploading package…'; this.error = '';
    this.api.uploadVersion(this.projectId, payload.file, payload.csv).subscribe({next: event => {
      if (event.type === HttpEventType.UploadProgress && event.total) this.notice = `Uploading package… ${Math.round(100 * event.loaded / event.total)}%`;
      if (event.type === HttpEventType.Response) this.changed('Working version uploaded. Preview it before publishing.');
    }, error: e => {this.busy=false;this.notice='';this.error=errorMessage(e);} });
  }
  publish(versionId: string): void { this.busy=true;this.api.publish(this.projectId, versionId).subscribe({next:()=>this.changed('Version published.', true),error:e=>{this.busy=false;this.error=errorMessage(e);}}); }
  publishPreview(): void {
    if (this.canPublishPreview && this.selectedVersion) this.publish(this.selectedVersion.id);
  }
  rollback(): void { this.busy=true;this.api.rollback(this.projectId).subscribe({next:()=>this.changed('Previous version restored.', true),error:e=>{this.busy=false;this.error=errorMessage(e);}}); }
  updateProject(changes: {name?:string;description?:string;allow_viewer_writes?:boolean}): void { this.busy=true;this.api.updateProject(this.projectId,changes).subscribe({next:()=>this.changed('Project settings saved.'),error:e=>{this.busy=false;this.error=errorMessage(e);}}); }
  addMember(member: {username:string;role:'editor'|'viewer'}): void {this.busy=true;this.api.setMember(this.projectId,member.username,member.role).subscribe({next:()=>this.changed('Access updated.'),error:e=>{this.busy=false;this.error=errorMessage(e);}});}
  removeMember(username: string): void {this.busy=true;this.api.removeMember(this.projectId,username).subscribe({next:()=>this.changed('Access removed.'),error:e=>{this.busy=false;this.error=errorMessage(e);}});}
  replaceCsv(file: File): void {this.busy=true;this.api.replaceCsv(this.projectId,file).subscribe({next:()=>this.changed('CSV replacement saved as a working version.'),error:e=>{this.busy=false;this.error=errorMessage(e);}});}
  importApiDataset(item: {connectionId:string;baseVersionId:string}): void {
    if (this.busy) return;
    this.busy = true; this.error = ''; this.notice = 'Importing API data…';
    this.api.importApiDataset(this.projectId, item.connectionId, item.baseVersionId).subscribe({
      next: result => {
        this.busy = false;
        this.notice = `API data imported into working version ${result.version_id.slice(0, 8)}. Preview it before publishing.`;
        this.reloadDetails();
        this.router.navigate([], { relativeTo: this.route, queryParams: { version: result.version_id } });
      },
      error: e => { this.busy = false; this.notice = ''; this.error = errorMessage(e); },
    });
  }
  loadData(): void {this.dataLoading=true;this.dataError='';this.api.snapshots(this.projectId).subscribe({next:d=>{this.snapshots=d.snapshots;this.dataLoading=false;},error:e=>{this.dataLoading=false;this.dataError=errorMessage(e);}});this.api.records(this.projectId).subscribe({next:d=>this.records=d.records,error:e=>this.dataError=errorMessage(e)});}
  createRecord(item: {data:Record<string,unknown>}): void {this.api.createRecord(this.projectId,item.data).subscribe({next:()=>this.loadData(),error:e=>this.dataError=errorMessage(e)});}
  updateRecord(item: {id:string;data:Record<string,unknown>;revision:number}): void {this.api.updateRecord(this.projectId,item.id,item.data,item.revision).subscribe({next:()=>this.loadData(),error:e=>this.dataError=errorMessage(e)});}
}
