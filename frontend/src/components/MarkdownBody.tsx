import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const remarkPlugins = [remarkGfm];

export default function MarkdownBody({ text, components }: { text: string; components: Components }) {
  return <Markdown remarkPlugins={remarkPlugins} components={components}>{text}</Markdown>;
}
