import React from 'react';
import { createRoot } from 'react-dom/client';
import StudyApp from './StudyApp';

const root = createRoot(document.getElementById('root')!);
root.render(<StudyApp />);

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then(regs => regs.forEach(r => r.unregister()));
}
