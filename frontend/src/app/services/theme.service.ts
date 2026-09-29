import { DOCUMENT } from '@angular/common';
import { DestroyRef, Injectable, inject, signal } from '@angular/core';

type Theme = 'light' | 'dark';
const STORAGE_KEY = 'agora-theme';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly document = inject(DOCUMENT);
  private readonly window = this.document.defaultView;
  private readonly system = this.window?.matchMedia('(prefers-color-scheme: dark)');
  private preference = this.readPreference();
  readonly theme = signal<Theme>(this.preference ?? this.systemTheme());

  constructor() {
    this.apply();
    const onSystemChange = () => {
      if (!this.preference) {
        this.theme.set(this.systemTheme());
        this.apply();
      }
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY && event.key !== null) return;
      this.preference = this.readPreference();
      this.theme.set(this.preference ?? this.systemTheme());
      this.apply();
    };
    this.system?.addEventListener('change', onSystemChange);
    this.window?.addEventListener('storage', onStorage);
    inject(DestroyRef).onDestroy(() => {
      this.system?.removeEventListener('change', onSystemChange);
      this.window?.removeEventListener('storage', onStorage);
    });
  }

  toggle(): void {
    this.preference = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(this.preference);
    try { this.window?.localStorage.setItem(STORAGE_KEY, this.preference); } catch { /* Keep the session theme when storage is unavailable. */ }
    this.apply();
  }

  private systemTheme(): Theme { return this.system?.matches ? 'dark' : 'light'; }

  private readPreference(): Theme | null {
    try {
      const stored = this.window?.localStorage.getItem(STORAGE_KEY);
      return stored === 'dark' || stored === 'light' ? stored : null;
    } catch { return null; }
  }

  private apply(): void {
    this.document.documentElement.dataset['theme'] = this.theme();
    this.document.querySelector('meta[name="theme-color"]')?.setAttribute('content', this.theme() === 'dark' ? '#0a1424' : '#f3f6fa');
  }
}
