import { Routes } from '@angular/router';
import { AuthPage } from './pages/auth.page';
import { SpacePage } from './pages/space.page';
import { ProjectPage } from './pages/project.page';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'space' },
  { path: 'login', component: AuthPage },
  { path: 'space', component: SpacePage },
  { path: 'projects/:id', component: ProjectPage },
  { path: '**', redirectTo: 'space' }
];
