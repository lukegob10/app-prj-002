import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ThemeService } from './services/theme.service';

@Component({ selector: 'agora-root', standalone: true, imports: [RouterOutlet], template: '<router-outlet />' })
export class App { private readonly theme = inject(ThemeService); }
