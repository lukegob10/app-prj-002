import { CommonModule } from '@angular/common';
import { Component, ElementRef, HostListener, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService } from '../services/api.service';
import { projectPath, spacePath } from '../project-paths';
import { AdminSupportComponent } from '../components/admin-support.component';
import { BrandComponent } from '../components/brand.component';
import { ThemeToggleComponent } from '../components/theme-toggle.component';

interface SpaceUser {
  id: string;
  username: string;
  full_name: string;
  is_admin: boolean;
}

interface SpaceProject {
  id: string;
  name: string;
  description: string | null;
  role: string;
  owner_id: string;
  owner_username: string;
  created_at: string;
  updated_at: string;
  allow_viewer_writes: boolean;
  published_version_id: string | null;
}

@Component({
  selector: 'agora-space-page',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, AdminSupportComponent, BrandComponent, ThemeToggleComponent],
  template: `
    <div class="space-page">
      <header class="space-header">
        <agora-brand />

        <div class="account-actions">
          <agora-theme-toggle />
          <ng-container *ngIf="user">
            <div class="account-menu" (keydown.escape)="showAccountMenu = false">
              <button
                type="button"
                class="account-menu-trigger"
                [attr.aria-label]="'Account options for ' + (user.full_name || user.username)"
                aria-controls="account-menu-panel"
                [attr.aria-expanded]="showAccountMenu"
                (click)="showAccountMenu = !showAccountMenu">
                <span class="account-avatar" aria-hidden="true">{{ accountInitials }}</span>
                <span class="account-chevron" aria-hidden="true">⌄</span>
              </button>
              <div class="account-menu-panel" id="account-menu-panel" [hidden]="!showAccountMenu" aria-label="Account options">
                <div class="account-identity">
                  <span class="account-identity-name">{{ user.full_name || user.username }}</span>
                  <span class="account-identity-handle">@{{ user.username }}</span>
                  <span class="admin-badge" *ngIf="user.is_admin">Administrator</span>
                </div>
                <button
                  *ngIf="user.is_admin"
                  type="button"
                  class="account-menu-item support-button"
                  (click)="showAdminSupport = !showAdminSupport; showAccountMenu = false"
                  [attr.aria-expanded]="showAdminSupport">
                  Account support
                </button>
                <button type="button" class="account-menu-item signout-button" (click)="signOut()" [disabled]="signingOut">
                  {{ signingOut ? 'Signing out…' : 'Sign out' }}
                </button>
              </div>
            </div>
          </ng-container>
        </div>
      </header>

      <main class="space-main">
        <div class="space-title-row">
          <div>
            <div class="eyebrow">Your workspace</div>
            <h1>My Space</h1>
            <p class="subtitle">Projects you own and work shared with you.</p>
          </div>
          <button
            *ngIf="!showCreateForm"
            type="button"
            class="primary-button create-toggle"
            (click)="openCreateForm()"
            [disabled]="authStateLoading">
            <span class="plus" aria-hidden="true">+</span>
            Create a project
          </button>
        </div>

        <agora-admin-support *ngIf="showAdminSupport && user?.is_admin" />

        <p class="alert error-alert" *ngIf="pageError" role="alert">
          {{ pageError }}
          <button type="button" class="inline-action" (click)="load()">Try again</button>
        </p>
        <p class="alert error-alert" *ngIf="signOutError" role="alert">{{ signOutError }}</p>

        <section class="create-panel" *ngIf="showCreateForm" aria-labelledby="create-title">
          <div class="create-heading">
            <div>
              <div class="eyebrow">Start something new</div>
              <h2 id="create-title">Create a project</h2>
            </div>
            <button type="button" class="icon-button" aria-label="Close project form" (click)="closeCreateForm()" [disabled]="creating">
              <span aria-hidden="true">×</span>
            </button>
          </div>

          <form class="create-form" (ngSubmit)="createProject()">
            <label class="field">
              <span>Project name</span>
              <input
                name="projectName"
                type="text"
                autocomplete="off"
                [(ngModel)]="projectName"
                required
                [disabled]="creating"
                placeholder="For example, Quarterly overview" />
            </label>
            <label class="field">
              <span>Description <span class="optional">Optional</span></span>
              <textarea
                name="projectDescription"
                rows="2"
                [(ngModel)]="projectDescription"
                [disabled]="creating"
                placeholder="A short note about what this project contains"></textarea>
            </label>
            <p class="form-error" *ngIf="createError" role="alert">{{ createError }}</p>
            <div class="form-actions">
              <button type="button" class="secondary-button" (click)="closeCreateForm()" [disabled]="creating">Cancel</button>
              <button type="submit" class="primary-button" [disabled]="creating">
                <span class="spinner light" *ngIf="creating" aria-hidden="true"></span>
                {{ creating ? 'Creating…' : 'Create project' }}
              </button>
            </div>
          </form>
        </section>

        <div class="loading-panel" *ngIf="authStateLoading || projectsLoading" role="status">
          <span class="spinner" aria-hidden="true"></span>
          Loading your space…
        </div>

        <div class="alert error-alert project-error" *ngIf="projectsError" role="alert">
          <span>{{ projectsError }}</span>
          <button type="button" class="inline-action" (click)="load()">Try again</button>
        </div>

        <div class="project-sections" *ngIf="showProjectSections">
          <section class="project-section" aria-labelledby="owned-title">
            <div class="section-heading">
              <div class="section-title">
                <h2 id="owned-title">My projects</h2>
                <span class="count-badge">{{ ownedProjects.length }}</span>
              </div>
              <p>Projects you created and manage.</p>
            </div>
            <div class="table-scroll" tabindex="0" role="region" aria-labelledby="owned-title">
              <table class="project-table" aria-labelledby="owned-title">
                <thead>
                  <tr><th scope="col">Project</th><th scope="col">Description</th><th scope="col">Status</th><th scope="col">Updated</th></tr>
                </thead>
                <tbody>
                  <tr *ngFor="let project of ownedProjects">
                    <th scope="row"><a class="project-link" [routerLink]="projectPath(project.owner_username, project.id)">{{ project.name }}</a></th>
                    <td class="description-cell">{{ project.description || 'No description added.' }}</td>
                    <td><span class="status-badge" [class.published]="project.published_version_id">{{ project.published_version_id ? 'Published' : 'Draft' }}</span></td>
                    <td class="date-cell">{{ project.updated_at ? (project.updated_at | date: 'mediumDate') : '—' }}</td>
                  </tr>
                  <tr *ngIf="!ownedProjects.length"><td class="table-empty" colspan="4">Your projects will appear here. <button type="button" class="inline-action" (click)="openCreateForm()">Create a project</button> to get started.</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="project-section" aria-labelledby="shared-title">
            <div class="section-heading">
              <div class="section-title">
                <h2 id="shared-title">Shared projects</h2>
                <span class="count-badge">{{ sharedProjects.length }}</span>
              </div>
              <p>Projects another person has shared with your account.</p>
            </div>
            <div class="table-scroll" tabindex="0" role="region" aria-labelledby="shared-title">
              <table class="project-table" aria-labelledby="shared-title">
                <thead>
                  <tr><th scope="col">Project</th><th scope="col">Description</th><th scope="col">Status</th><th scope="col">Your role</th><th scope="col">Updated</th></tr>
                </thead>
                <tbody>
                  <tr *ngFor="let project of sharedProjects">
                    <th scope="row"><a class="project-link" [routerLink]="projectPath(project.owner_username, project.id)">{{ project.name }}</a></th>
                    <td class="description-cell">{{ project.description || 'No description added.' }}</td>
                    <td><span class="status-badge" [class.published]="project.published_version_id">{{ project.published_version_id ? 'Published' : 'Draft' }}</span></td>
                    <td class="role-cell">{{ project.role | titlecase }}</td>
                    <td class="date-cell">{{ project.updated_at ? (project.updated_at | date: 'mediumDate') : '—' }}</td>
                  </tr>
                  <tr *ngIf="!sharedProjects.length"><td class="table-empty" colspan="5">No shared projects yet. Projects shared with your account will appear here.</td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </main>
    </div>
  `,
  styles: [`
    :host { display: block; min-height: 100%; color: var(--text); }
    .space-page { min-height: 100vh; background: var(--bg); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .space-header { box-sizing: border-box; min-height: 68px; padding: 12px clamp(20px, 5vw, 74px); display: flex; align-items: center; justify-content: space-between; gap: 20px; border-bottom: 1px solid var(--border); background: var(--surface); }
    .account-actions { display: flex; justify-content: flex-end; align-items: center; gap: 13px; min-width: 0; }
    .account-menu { position: relative; }
    .account-menu-trigger { display: inline-flex; align-items: center; gap: 6px; padding: 2px; border: 0; border-radius: 9px; background: transparent; color: var(--text-muted); cursor: pointer; }
    .account-menu-trigger:hover { color: var(--text); }
    .account-avatar { display: grid; width: 35px; height: 35px; place-items: center; border: 1px solid var(--border-strong); border-radius: 50%; background: var(--surface-raised); color: var(--accent-text); font-size: 11px; font-weight: 750; letter-spacing: .025em; }
    .account-menu-trigger:hover .account-avatar, .account-menu-trigger[aria-expanded="true"] .account-avatar { border-color: var(--accent); background: var(--accent-soft); }
    .account-chevron { font-size: 15px; line-height: 1; }
    .account-menu-panel { position: absolute; z-index: 20; top: calc(100% + 9px); right: 0; width: min(250px, calc(100vw - 34px)); padding: 8px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--surface); box-shadow: var(--shadow); }
    .account-menu-panel[hidden] { display: none; }
    .account-identity { display: grid; gap: 3px; padding: 8px 9px 11px; }
    .account-identity-name { overflow: hidden; color: var(--text); font-size: 13px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
    .account-identity-handle { color: var(--text-muted); font-size: 11px; }
    .admin-badge { padding: 4px 8px; border: 1px solid var(--border); border-radius: 99px; color: var(--accent); font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
    .account-identity .admin-badge { justify-self: start; margin-top: 5px; }
    .account-menu-item { display: flex; width: 100%; min-height: 37px; align-items: center; padding: 8px 9px; border: 0; border-radius: 6px; background: transparent; color: var(--text-muted); font: inherit; font-size: 12px; font-weight: 600; text-align: left; cursor: pointer; }
    .account-menu-item:hover:not(:disabled) { background: var(--surface-hover); color: var(--text); }
    .account-menu-item.signout-button { justify-content: flex-start; margin-top: 4px; border-top: 1px solid var(--border); border-radius: 0 0 6px 6px; color: var(--text-muted); }
    .signout-button, .secondary-button { min-height: 35px; padding: 7px 11px; border: 1px solid var(--border-strong); border-radius: 6px; background: transparent; color: var(--text-muted); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; transition: border-color .15s ease, color .15s ease, background .15s ease; }
    .signout-button:hover:not(:disabled), .secondary-button:hover:not(:disabled) { border-color: var(--accent); background: var(--surface-hover); color: var(--text); }
    .signout-button:disabled, .secondary-button:disabled { opacity: .55; cursor: wait; }
    .space-main { width: min(1160px, calc(100% - 48px)); margin: 0 auto; padding: 49px 0 76px; }
    .space-title-row { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 33px; }
    .eyebrow { margin-bottom: 8px; color: var(--text-subtle); font-size: 10px; font-weight: 700; letter-spacing: .13em; text-transform: uppercase; }
    h1 { margin: 0; color: var(--text); font-size: clamp(27px, 3vw, 34px); font-weight: 650; letter-spacing: -.035em; line-height: 1.2; }
    .subtitle { margin: 9px 0 0; color: var(--text-muted); font-size: 14px; line-height: 1.5; }
    .primary-button { display: inline-flex; justify-content: center; align-items: center; gap: 8px; min-height: 39px; padding: 9px 14px; border: 1px solid var(--accent); border-radius: 6px; background: var(--primary-bg); color: var(--primary-text); font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; transition: background .15s ease, border-color .15s ease, transform .15s ease; }
    .primary-button:hover:not(:disabled) { border-color: var(--primary-hover); background: var(--primary-hover); }
    .primary-button:active:not(:disabled) { transform: translateY(1px); }
    .primary-button:disabled { opacity: .56; cursor: wait; }
    .plus { font-size: 19px; font-weight: 400; line-height: .75; }
    .create-toggle { min-height: 41px; padding-inline: 16px; }
    .create-panel { max-width: 660px; margin: -7px 0 33px auto; padding: 22px 24px 21px; border: 1px solid var(--border); border-radius: 11px; background: var(--surface-raised); box-shadow: var(--shadow); }
    .create-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 17px; }
    .create-heading .eyebrow { margin-bottom: 5px; }
    h2 { margin: 0; color: var(--text); font-size: 17px; font-weight: 640; letter-spacing: -.015em; }
    .icon-button { width: 31px; height: 31px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--text-muted); font: inherit; font-size: 21px; line-height: 1; cursor: pointer; }
    .icon-button:hover:not(:disabled) { border-color: var(--border-strong); color: var(--text); }
    .icon-button:disabled { opacity: .5; cursor: wait; }
    .create-form { display: grid; gap: 14px; }
    .field { display: grid; gap: 6px; color: var(--text); font-size: 12px; font-weight: 600; }
    .optional { margin-left: 5px; color: var(--text-subtle); font-size: 11px; font-weight: 400; }
    input, textarea { box-sizing: border-box; width: 100%; padding: 9px 11px; border: 1px solid var(--border-strong); border-radius: 6px; outline: none; background: var(--bg); color: var(--text); font: inherit; font-size: 13px; line-height: 1.45; transition: border-color .15s ease, box-shadow .15s ease; }
    input { min-height: 39px; }
    textarea { min-height: 66px; resize: vertical; }
    input::placeholder, textarea::placeholder { color: var(--text-subtle); }
    input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
    input:disabled, textarea:disabled { opacity: .68; }
    .form-error { margin: 0; color: var(--error-text); font-size: 12px; }
    .form-actions { display: flex; justify-content: flex-end; align-items: center; gap: 9px; padding-top: 2px; }
    .form-actions .primary-button { min-width: 126px; }
    .alert { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin: 0 0 22px; padding: 11px 13px; border-radius: 7px; font-size: 13px; line-height: 1.5; }
    .error-alert { border: 1px solid var(--error-border); background: var(--error-bg); color: var(--error-text); }
    .inline-action { padding: 1px 3px; border: 0; background: transparent; color: var(--accent); font: inherit; font-weight: 700; text-decoration: underline; cursor: pointer; }
    .error-alert .inline-action { color: inherit; }
    .project-error { justify-content: space-between; }
    .loading-panel { display: flex; align-items: center; gap: 10px; min-height: 105px; color: var(--text-muted); font-size: 13px; }
    .spinner { width: 15px; height: 15px; flex: 0 0 auto; border: 2px solid var(--border-strong); border-top-color: var(--accent); border-radius: 50%; animation: spin .75s linear infinite; }
    .spinner.light { width: 12px; height: 12px; border-color: var(--accent-soft); border-top-color: var(--accent-text); }
    @keyframes spin { to { transform: rotate(360deg); } }
    .project-sections { display: grid; gap: 36px; }
    .project-section { min-width: 0; }
    .section-heading { margin-bottom: 14px; }
    .section-title { display: flex; align-items: center; gap: 10px; }
    .section-title h2 { font-size: 16px; }
    .count-badge { min-width: 20px; padding: 3px 6px; border: 1px solid var(--border); border-radius: 99px; background: var(--surface); color: var(--text-muted); font-size: 10px; font-weight: 700; text-align: center; }
    .section-heading p { margin: 5px 0 0; color: var(--text-subtle); font-size: 12px; }
    .table-scroll { overflow-x: auto; border: 1px solid var(--border-strong); border-radius: 9px; background: var(--surface); box-shadow: 0 3px 12px rgb(28 48 72 / 4%); }
    .project-table { width: 100%; min-width: 640px; border-collapse: collapse; text-align: left; font-size: 13px; line-height: 1.5; }
    .project-table[aria-labelledby="shared-title"] { min-width: 750px; }
    .project-table th, .project-table td { padding: 15px 18px; border-bottom: 1px solid var(--border); vertical-align: top; }
    .project-table thead th { padding-block: 13px; border-bottom-color: var(--border-strong); background: var(--surface-raised); color: var(--text-muted); font-size: 10px; font-weight: 700; letter-spacing: .045em; text-transform: uppercase; white-space: nowrap; }
    .project-table tbody tr { background: var(--surface); transition: background .12s ease; }
    .project-table tbody tr:nth-child(even) { background: var(--bg); }
    .project-table tbody th { width: 26%; font-size: 13px; font-weight: 650; }
    .project-table tbody tr:last-child > * { border-bottom: 0; }
    .project-table tbody tr:hover { background: var(--surface-hover); }
    .project-link { color: var(--text); text-decoration: none; overflow-wrap: anywhere; }
    .project-link:hover { color: var(--accent); text-decoration: underline; }
    .description-cell { width: 40%; color: var(--text-muted); overflow-wrap: anywhere; }
    .date-cell, .role-cell { color: var(--text-muted); white-space: nowrap; }
    .status-badge { display: inline-block; padding: 2px 8px; border-radius: 99px; background: var(--bg); border: 1px solid var(--border-strong); color: var(--text); font-size: 11px; font-weight: 650; }
    .status-badge.published { border-color: transparent; background: var(--success-bg); color: var(--success-text); }
    .project-table .table-empty { padding: 24px 18px; color: var(--text-muted); }
    button:focus-visible, a:focus-visible, .table-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
    @media (max-width: 600px) {
      .space-header { min-height: 60px; padding-inline: 17px; align-items: center; }
      .account-actions { gap: 9px; }
      .space-main { width: calc(100% - 34px); padding-top: 34px; }
      .space-title-row { align-items: flex-start; flex-direction: column; gap: 18px; margin-bottom: 25px; }
      .create-toggle { align-self: stretch; }
      .create-panel { margin: 0 0 25px; padding: 18px; }
      .project-sections { gap: 31px; }
      .project-table th, .project-table td { padding: 13px 14px; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
    }
  `],
})
export class SpacePage implements OnInit {
  readonly projectPath = projectPath;
  user: SpaceUser | null = null;
  ownedProjects: SpaceProject[] = [];
  sharedProjects: SpaceProject[] = [];

  authStateLoading = true;
  projectsLoading = false;
  creating = false;
  signingOut = false;
  showCreateForm = false;
  showAdminSupport = false;
  showAccountMenu = false;

  projectName = '';
  projectDescription = '';
  pageError = '';
  projectsError = '';
  createError = '';
  signOutError = '';

  constructor(private readonly api: ApiService, private readonly router: Router, private readonly host: ElementRef<HTMLElement>) {}

  ngOnInit(): void {
    this.load();
  }

  @HostListener('document:click', ['$event'])
  closeAccountMenuOnOutsideClick(event: MouseEvent): void {
    const menu = this.host.nativeElement.querySelector('.account-menu');
    if (event.target instanceof Node && !menu?.contains(event.target)) {
      this.showAccountMenu = false;
    }
  }

  get accountInitials(): string {
    const parts = (this.user?.full_name || this.user?.username || 'A').trim().split(/\s+/).filter(Boolean);
    if (parts.length > 1) return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
    return (parts[0] || 'A').slice(0, 2).toUpperCase();
  }

  get showProjectSections(): boolean {
    return !this.authStateLoading && !this.projectsLoading && !this.pageError && !this.projectsError;
  }

  load(): void {
    this.pageError = '';
    this.projectsError = '';
    this.authStateLoading = true;
    this.projectsLoading = false;

    this.api.auth().subscribe({
      next: (auth) => {
        this.authStateLoading = false;
        if (!auth) {
          void this.router.navigateByUrl('/login');
          return;
        }
        this.user = auth.user;
        if (this.router.url.split(/[?#]/, 1)[0] !== spacePath(auth.user.username)) {
          void this.router.navigateByUrl(spacePath(auth.user.username), { replaceUrl: true });
        }
        this.loadProjects();
      },
      error: () => {
        this.authStateLoading = false;
        this.pageError = 'We could not verify your session. Check your connection and try again.';
      },
    });
  }

  openCreateForm(): void {
    this.createError = '';
    this.showCreateForm = true;
  }

  closeCreateForm(): void {
    if (this.creating) return;
    this.showCreateForm = false;
    this.createError = '';
    this.projectName = '';
    this.projectDescription = '';
  }

  createProject(): void {
    if (this.creating) return;
    const name = this.projectName.trim();
    const description = this.projectDescription.trim();
    if (!name) {
      this.createError = 'Enter a name for this project.';
      return;
    }

    this.createError = '';
    this.creating = true;
    this.api.createProject(name, description).subscribe({
      next: (project) => {
        this.creating = false;
        this.ownedProjects = [project, ...this.ownedProjects];
        this.projectName = '';
        this.projectDescription = '';
        this.showCreateForm = false;
        void this.router.navigateByUrl(projectPath(project.owner_username, project.id));
      },
      error: (error: unknown) => {
        this.creating = false;
        this.createError = this.readErrorMessage(error) ?? 'We could not create this project. Please try again.';
      },
    });
  }

  signOut(): void {
    if (this.signingOut) return;
    this.showAccountMenu = false;
    this.signingOut = true;
    this.signOutError = '';
    this.api.logout().subscribe({
      next: () => {
        this.signingOut = false;
        void this.router.navigateByUrl('/login');
      },
      error: (error: unknown) => {
        this.signingOut = false;
        this.signOutError = this.readErrorMessage(error) ?? 'We could not sign you out. Please try again.';
      },
    });
  }

  private loadProjects(): void {
    this.projectsLoading = true;
    this.api.listProjects().subscribe({
      next: (result) => {
        this.ownedProjects = result.owned;
        this.sharedProjects = result.shared;
        this.projectsLoading = false;
      },
      error: (error: unknown) => {
        this.projectsLoading = false;
        this.projectsError = this.readErrorMessage(error) ?? 'We could not load your projects. Please try again.';
      },
    });
  }

  private readErrorMessage(error: unknown): string | null {
    if (!error || typeof error !== 'object') return null;
    const body = (error as { error?: unknown }).error;
    if (!body || typeof body !== 'object') return null;
    const envelope = body as { error?: { message?: unknown }; message?: unknown };
    if (typeof envelope.error?.message === 'string') return envelope.error.message;
    if (typeof envelope.message === 'string') return envelope.message;
    return null;
  }
}

