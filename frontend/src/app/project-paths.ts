export function spacePath(username: string): string {
  const segment = /^(login|space)$/i.test(username) ? `@${username}` : username;
  return `/${encodeURIComponent(segment)}`;
}

export function projectPath(ownerUsername: string, projectId: string): string {
  return `${spacePath(ownerUsername)}/projects/${encodeURIComponent(projectId)}`;
}
