import { HocuspocusProvider } from '@hocuspocus/provider';
import * as Y from 'yjs';

export function createCollabProvider(
  documentName: string,
  token: string,
  ydoc?: Y.Doc
) {
  const doc = ydoc || new Y.Doc();

  const provider = new HocuspocusProvider({
    url: process.env.NEXT_PUBLIC_COLLAB_URL || 'ws://localhost:1234',
    name: documentName,
    document: doc,
    token,
    onConnect: () => {
      console.log('[Collab] Connected to document:', documentName);
    },
    onDisconnect: () => {
      console.log('[Collab] Disconnected from document:', documentName);
    },
    onSynced: () => {
      console.log('[Collab] Document synced:', documentName);
    },
  });

  return { provider, doc };
}
