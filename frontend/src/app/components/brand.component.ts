import { Component, Input, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ThemeService } from '../services/theme.service';
import { ApiService } from '../services/api.service';
import { spacePath } from '../project-paths';

@Component({
  selector: 'agora-brand',
  standalone: true,
  imports: [RouterLink],
  template: `
    <a class="brand" [class.compact-mobile]="compactOnMobile" [routerLink]="destination || spacePath(api.currentUser()?.username || '')" [attr.aria-label]="label">
      <span class="logo-full" [class.dark]="theme.theme() === 'dark'" aria-hidden="true">
        <img class="logo-wordmark" src="/brand/agora-wordmark-color.png" width="512" height="144" alt="" />
        @if (theme.theme() === 'dark') {
          <img class="logo-mark-overlay" src="/brand/agora-mark-color.png" width="256" height="256" alt="" />
        }
      </span>
      <img class="logo-compact" src="/brand/agora-mark-color.png" width="256" height="256" alt="" aria-hidden="true" />
    </a>
  `,
  styles: [`
    :host { display: inline-flex; flex: 0 0 auto; }
    .brand { display: inline-flex; align-items: center; width: 124px; height: 36px; text-decoration: none; }
    .logo-full { position: relative; display: block; width: 124px; height: 35px; }
    .logo-wordmark { display: block; width: 100%; height: 100%; object-fit: contain; }
    .logo-full.dark .logo-wordmark { filter: brightness(0) invert(1); clip-path: inset(0 0 0 34%); }
    .logo-mark-overlay { position: absolute; top: 0; left: 0; width: 35px; height: 35px; object-fit: contain; }
    .logo-compact { display: none; width: 34px; height: 34px; object-fit: contain; }
    @media(max-width: 620px) {
      .brand.compact-mobile { width: 34px; }
      .brand.compact-mobile .logo-full { display: none; }
      .brand.compact-mobile .logo-compact { display: block; }
    }
  `]
})
export class BrandComponent {
  @Input() destination: string | null = null;
  @Input() label = 'Agora, My Space';
  @Input() compactOnMobile = false;
  readonly theme = inject(ThemeService);
  readonly api = inject(ApiService);
  readonly spacePath = spacePath;
}
