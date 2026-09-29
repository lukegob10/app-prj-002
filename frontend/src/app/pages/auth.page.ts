import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService } from '../services/api.service';
import { BrandComponent } from '../components/brand.component';
import { ThemeToggleComponent } from '../components/theme-toggle.component';

@Component({
  selector: 'agora-auth-page',
  standalone: true,
  imports: [CommonModule, FormsModule, BrandComponent, ThemeToggleComponent],
  template: `
    <main class="auth-page">
      <div class="auth-frame">
        <header class="auth-header">
        <agora-brand destination="/login" label="Agora sign in" />
        <agora-theme-toggle />
        </header>

        <section class="auth-card" aria-labelledby="auth-title">
          <div class="eyebrow">A calm place for your dashboards</div>
          <h1 id="auth-title">{{ isRegistering ? 'Create your account' : 'Welcome back' }}</h1>
          <p class="intro">
            {{ isRegistering
              ? 'Set up an account to create and share your project space.'
              : 'Sign in to continue to your projects.' }}
          </p>

          <div class="session-check" *ngIf="checkingSession" role="status">
            <span class="spinner" aria-hidden="true"></span>
            Checking your session…
          </div>

          <p class="form-message error-message" *ngIf="errorMessage" role="alert">
            {{ errorMessage }}
          </p>

          <form (ngSubmit)="submit()" novalidate>
            <label class="field">
              <span>Username</span>
              <input
                name="username"
                type="text"
                autocomplete="username"
                [(ngModel)]="username"
                required
                [disabled]="busy || checkingSession"
                placeholder="Your username" />
            </label>

            <label class="field" *ngIf="isRegistering">
              <span>Full name</span>
              <input
                name="fullName"
                type="text"
                autocomplete="name"
                [(ngModel)]="fullName"
                required
                [disabled]="busy || checkingSession"
                placeholder="How people will see your name" />
            </label>

            <label class="field">
              <span>Password</span>
              <input
                name="password"
                type="password"
                [autocomplete]="isRegistering ? 'new-password' : 'current-password'"
                [(ngModel)]="password"
                required
                [disabled]="busy || checkingSession"
                placeholder="Enter your password" />
            </label>

            <button class="primary-button submit-button" type="submit" [disabled]="busy || checkingSession">
              <span class="spinner light" *ngIf="busy" aria-hidden="true"></span>
              {{ busy ? (isRegistering ? 'Creating account…' : 'Signing in…') : (isRegistering ? 'Create account' : 'Sign in') }}
            </button>
          </form>

          <div class="auth-switch">
            <span>{{ isRegistering ? 'Already have an account?' : 'New to Agora?' }}</span>
            <button type="button" class="text-button" (click)="toggleMode()" [disabled]="busy || checkingSession">
              {{ isRegistering ? 'Sign in' : 'Create an account' }}
            </button>
          </div>

          <p class="recovery-note" *ngIf="!isRegistering">
            Need account help? Contact your Agora administrator.
          </p>
        </section>

        <p class="page-footnote">Your projects stay organized in one shared space.</p>
      </div>
    </main>
  `,
  styles: [`
    :host { display: block; min-height: 100%; color: var(--text); }
    .auth-page {
      min-height: 100vh;
      box-sizing: border-box;
      padding: 40px 20px 28px;
      display: grid;
      place-items: center;
      background:
        radial-gradient(ellipse at 50% 0%, rgba(41, 74, 112, .23), transparent 47%),
        var(--bg);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .auth-frame { width: min(100%, 418px); }
    .auth-header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 25px; }
    .auth-card {
      padding: 35px 36px 27px;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .eyebrow {
      margin-bottom: 10px;
      color: var(--accent-text);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .11em;
      text-transform: uppercase;
    }
    h1 { margin: 0; color: var(--text); font-size: 25px; line-height: 1.25; font-weight: 650; letter-spacing: -.025em; }
    .intro { margin: 9px 0 24px; color: var(--text-muted); font-size: 14px; line-height: 1.55; }
    form { display: grid; gap: 17px; }
    .field { display: grid; gap: 7px; color: var(--text); font-size: 13px; font-weight: 550; }
    input {
      box-sizing: border-box;
      width: 100%;
      min-height: 43px;
      padding: 10px 12px;
      border: 1px solid var(--border-strong);
      border-radius: 7px;
      outline: none;
      background: var(--input-bg);
      color: var(--text);
      font: inherit;
      font-size: 14px;
      transition: border-color .15s ease, box-shadow .15s ease;
    }
    input::placeholder { color: var(--text-subtle); }
    input:focus { border-color: var(--focus); box-shadow: 0 0 0 3px rgba(90, 145, 205, .18); }
    input:disabled { opacity: .7; }
    .primary-button {
      display: inline-flex;
      justify-content: center;
      align-items: center;
      gap: 9px;
      min-height: 43px;
      padding: 10px 15px;
      border: 1px solid var(--primary-bg);
      border-radius: 7px;
      background: var(--primary-bg);
      color: var(--primary-text);
      font: inherit;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      transition: background .15s ease, border-color .15s ease, transform .15s ease;
    }
    .primary-button:hover:not(:disabled) { border-color: var(--primary-hover); background: var(--primary-hover); }
    .primary-button:active:not(:disabled) { transform: translateY(1px); }
    .primary-button:disabled { opacity: .56; cursor: wait; }
    .submit-button { margin-top: 3px; }
    .auth-switch {
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      align-items: center;
      gap: 5px;
      margin-top: 23px;
      color: var(--text-muted);
      font-size: 13px;
    }
    .text-button {
      padding: 3px 2px;
      border: 0;
      background: transparent;
      color: var(--accent-text);
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    .text-button:hover:not(:disabled) { color: var(--accent-hover); text-decoration: underline; }
    .text-button:disabled { opacity: .55; cursor: wait; }
    .recovery-note {
      margin: 18px 0 0;
      padding-top: 16px;
      border-top: 1px solid var(--border);
      color: var(--text-subtle);
      font-size: 12px;
      line-height: 1.5;
      text-align: center;
    }
    .page-footnote { margin: 18px 0 0; color: var(--text-subtle); font-size: 12px; text-align: center; }
    .form-message { margin: 0 0 17px; padding: 10px 12px; border-radius: 7px; font-size: 13px; line-height: 1.45; }
    .error-message { border: 1px solid var(--error-border); background: var(--error-bg); color: var(--error-text); }
    .session-check { display: flex; align-items: center; gap: 8px; margin: -4px 0 17px; color: var(--text-muted); font-size: 12px; }
    .spinner {
      width: 14px;
      height: 14px;
      flex: 0 0 auto;
      border: 2px solid rgba(169, 201, 237, .3);
      border-top-color: var(--accent-text);
      border-radius: 50%;
      animation: spin .75s linear infinite;
    }
    .spinner.light { width: 13px; height: 13px; border-color: rgb(255 255 255 / 30%); border-top-color: var(--primary-text); }
    @keyframes spin { to { transform: rotate(360deg); } }
    button:focus-visible, a:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
    @media (max-width: 480px) {
      .auth-page { padding: 28px 15px 22px; }
      .auth-card { padding: 29px 23px 23px; }
      h1 { font-size: 23px; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
    }
  `],
})
export class AuthPage implements OnInit {
  username = '';
  fullName = '';
  password = '';
  isRegistering = false;
  busy = false;
  checkingSession = true;
  errorMessage = '';

  constructor(private readonly api: ApiService, private readonly router: Router) {}

  ngOnInit(): void {
    this.api.auth().subscribe({
      next: (auth) => {
        this.checkingSession = false;
        if (auth) void this.router.navigateByUrl('/space');
      },
      error: () => {
        this.checkingSession = false;
        this.errorMessage = 'We could not check your session. You can still try signing in.';
      },
    });
  }

  toggleMode(): void {
    this.isRegistering = !this.isRegistering;
    this.errorMessage = '';
  }

  submit(): void {
    if (this.busy || this.checkingSession) return;
    const username = this.username.trim();
    const fullName = this.fullName.trim();
    if (!username || !this.password || (this.isRegistering && !fullName)) {
      this.errorMessage = 'Complete each required field to continue.';
      return;
    }

    this.errorMessage = '';
    this.busy = true;
    const request = this.isRegistering
      ? this.api.register(username, fullName, this.password)
      : this.api.login(username, this.password);

    request.subscribe({
      next: () => {
        this.busy = false;
        void this.router.navigateByUrl('/space');
      },
      error: (error: unknown) => {
        this.busy = false;
        this.errorMessage = this.readErrorMessage(error) ??
          (this.isRegistering ? 'We could not create your account. Check your details and try again.' : 'We could not sign you in. Check your details and try again.');
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
