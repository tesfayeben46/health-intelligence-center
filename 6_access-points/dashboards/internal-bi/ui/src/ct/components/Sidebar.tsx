import { IconTower, IconSummary, IconMovement, IconRacking, IconAgainstTarget,
  IconCaseFinding, IconExportRows } from '../icons';

const CONTROL = [
  { id: 'overview', label: 'Overview', Icon: IconSummary },
  { id: 'movement', label: 'Movement planning', Icon: IconMovement, active: true },
];
const ANALYTICS = [
  { id: 'resource', label: 'Resource management', Icon: IconRacking },
  { id: 'service', label: 'Service performance', Icon: IconAgainstTarget },
  { id: 'disease', label: 'Disease surveillance', Icon: IconCaseFinding },
];
const TOOLS = [
  { id: 'export', label: 'Data export & query', Icon: IconExportRows },
];

function NavItem({ label, Icon, active }: { label: string; Icon: any; active?: boolean }) {
  return (
    <a className={`bi-nav-a${active ? ' active' : ''}`}>
      <Icon className="ico" /><span>{label}</span>
    </a>
  );
}

export default function Sidebar() {
  return (
    <aside className="bi-sidebar">
      <div className="bi-sidehead">
        <a className="bi-brand">
          <span className="bi-brand-mark"><IconTower /></span>
          <span className="bi-brand-text">
            <span className="title">PCT</span>
            <span className="subtitle">Supply Control Tower</span>
          </span>
        </a>
      </div>

      <div className="bi-nav-group">
        <h5>Planning</h5>
        <nav className="bi-nav">{CONTROL.map(n => <NavItem key={n.id} {...n} />)}</nav>
      </div>

      <div className="bi-nav-group">
        <h5>Analytics</h5>
        <nav className="bi-nav">{ANALYTICS.map(n => <NavItem key={n.id} {...n} />)}</nav>
      </div>

      <div className="bi-nav-group">
        <h5>Data &amp; Tools</h5>
        <nav className="bi-nav">{TOOLS.map(n => <NavItem key={n.id} {...n} />)}</nav>
      </div>

    </aside>
  );
}
