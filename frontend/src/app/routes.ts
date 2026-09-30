import { Routes } from '@angular/router';
import { AuthPage } from './pages/auth.page';
import { SpacePage } from './pages/space.page';
import { ProjectPage } from './pages/project.page';

export const routes: Routes = [
  { path: '', pathMatch: 'full', component: SpacePage },
  { path: 'login', component: AuthPage },
  { path: 'space', component: SpacePage },
  { path: 'projects/:id', component: ProjectPage },
  { path: ':username/projects/:id', component: ProjectPage },
  { path: ':username', component: SpacePage },
  { path: '**', redirectTo: '' }
];
