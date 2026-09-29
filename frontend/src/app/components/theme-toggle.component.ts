import { Component, inject } from '@angular/core';
import { ThemeService } from '../services/theme.service';

@Component({
  selector: 'agora-theme-toggle',
  standalone: true,
  template: `
    <button
      class="theme-toggle"
      type="button"
      (click)="theme.toggle()"
      [attr.aria-label]="theme.theme() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'"
      [title]="theme.theme() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'">
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        @if (theme.theme() === 'dark') {
          <path d="M20.8 13a9 9 0 0 1-9.8-9.8A9 9 0 1 0 20.8 13Z" />
        } @else {
          <circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        }
      </svg>
      <span>{{ theme.theme() === 'dark' ? 'Dark mode' : 'Light mode' }}</span>
    </button>
  `,
  styles: [`
    :host { display: inline-flex; }
    .theme-toggle { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 36px; border: 1px solid var(--border-strong); border-radius: 999px; padding: 7px 12px; background: var(--surface-raised); color: var(--text); font-size: .75rem; font-weight: 650; white-space: nowrap; transition: background-color .15s ease, border-color .15s ease, color .15s ease; }
    .theme-toggle svg { flex: 0 0 auto; color: var(--accent-text); }
    .theme-toggle:hover { border-color: var(--accent); background: var(--surface-hover); }
    .theme-toggle:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
    @media(max-width: 420px) { .theme-toggle { width: 36px; padding: 0; } .theme-toggle span { display: none; } }
    @media(prefers-reduced-motion: reduce) { .theme-toggle { transition: none; } }
  `]
})
export class ThemeToggleComponent {
  readonly theme = inject(ThemeService);
}
