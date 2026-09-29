interface AgoraBridge {
  ready: Promise<{projectId:string;versionId:string;capabilities:string[]}>;
  destroy(): void;
}
interface Window {
  AgoraHost?: {
    createAgoraViewerBridge(options: {
      iframe: HTMLIFrameElement; src: string; projectId: string; versionId: string;
      grantToken: string; capabilities: string[]; getCsrfToken: () => string | null;
      onReady?: () => void; onError?: (error: {message?:string;code?:string}) => void;
    }): AgoraBridge;
  };
}
