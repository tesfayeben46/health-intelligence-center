export default function DemoValue({ value }: { value: number }) {
  return (
    <span className="demo-value-bar" title={`demo value ${value}/5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} className={n <= value ? 'on' : ''} />
      ))}
    </span>
  );
}
