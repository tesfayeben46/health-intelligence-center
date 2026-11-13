/**
 * Workshop footer. The community Code of Conduct sits here as a compact,
 * low-key row so the guidance stays visible without dominating every page.
 */
import { useProfile } from '../hooks/useProfile';

export default function Footer() {
  const profile = useProfile();
  // The owner line comes from the profile's copy deck (`workshop.footer_owner`,
  // served already resolved by the gateway) so each profile controls its
  // wording. Composing it from ministry and country names is only the fallback.
  const owner = profile.workshop?.footer_owner
    || [profile.ministry?.name, profile.country?.official_name].filter(Boolean).join(' · ');
  return (
    <footer className="foot">
      <div className="foot-inner">
        <div>
          <strong>Community Workshop Portal · {profile.platform?.abbr || 'NHDW'}</strong><br />
          {owner ? <>{owner}<br /></> : null}
          <span className="foot-byline">For researchers, developers, partners, and the open public</span>
        </div>
        <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', fontSize: '0.85rem' }}>
          <div><strong>Learn</strong><br /><a href="#tutorials">Tutorials</a><br /><a href="#workshops">Workshops</a><br /><a href="#openfn">OpenFn</a></div>
          <div><strong>Build</strong><br /><a href="#datasets">Datasets</a><br /><a href="#">API docs</a><br /><a href="#">Code repo</a></div>
          <div><strong>Connect</strong><br /><a href="#community">Discussions</a><br /><a href="#mentorship">Mentorship</a><br /><a href="/">← Public site</a></div>
        </div>
      </div>
      <div className="foot-coc">
        <svg className="ico"><use href="#i-policy" /></svg>
        <span>
          Contribute responsibly. Personal data, even when k-anonymised, must be handled with care and never combined with other sources to re-identify individuals.
        </span>
        <a href="#coc"><svg className="ico"><use href="#i-policy" /></svg> Code of conduct</a>
        <a href="#data-use"><svg className="ico"><use href="#i-list" /></svg> Data use agreement</a>
      </div>
    </footer>
  );
}
