import { createRoot } from 'react-dom/client';
// Self-hosted UI font (the cluster may be offline, so no runtime CDN). Weights used by the
// chrome: 400 body · 600 semibold · 700 bold · 800 stat numerals.
import '@fontsource/nunito-sans/400.css';
import '@fontsource/nunito-sans/600.css';
import '@fontsource/nunito-sans/700.css';
import '@fontsource/nunito-sans/800.css';
import './styles/base.css';
import './styles/home.css';
import './styles/x-cloak.css';
import App from './App';

// Not wrapped in StrictMode: the imperative renderActive() must fire exactly
// once per active-page change. The paginated_table renderer sets
// `ws.sort = options[0]` after its first render, so a second call re-sorts the rows.
createRoot(document.getElementById('root')!).render(<App />);
