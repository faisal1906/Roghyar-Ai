import { createRoot } from 'react-dom/client';
import { PrivateAdmin } from './private-admin';

const root = document.getElementById('root');
if (import.meta.env.DEV && root) createRoot(root).render(<PrivateAdmin />);
