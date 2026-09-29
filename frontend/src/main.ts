import 'zone.js';
import { provideZoneChangeDetection } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { App } from './app/app';
import { routes } from './app/routes';

bootstrapApplication(App, { providers: [provideZoneChangeDetection(), provideHttpClient(), provideRouter(routes)] }).catch(console.error);
