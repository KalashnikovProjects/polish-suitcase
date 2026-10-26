/** Small safe subset for model emphasis; text is escaped by React, HTML is never injected. */
export default function MessageText({ text = '' }: { text?: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part)}</>;
}
