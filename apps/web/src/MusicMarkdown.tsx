import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type {Citation} from '../../../packages/contracts/types';

export function MusicMarkdown({text,citations=[],onLocate}:{text:string;citations?:Citation[];onLocate?:(citation:Citation)=>void}){
  return <div className="music-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml
    urlTransform={url=>/^(https?:|mailto:|#)/i.test(url)||/^music:\/\/citation\/[a-f0-9]{32}$/.test(url)?url:''}
    components={{a:({href,children})=>{
      if(href?.startsWith('music://citation/')){const citation=citations.find(c=>'music://citation/'+c.id===href);return citation&&onLocate?<button className="inline-citation" onClick={()=>onLocate(citation)}>{children} ↗</button>:<span>{children}</span>;}
      return href?<a href={href} target="_blank" rel="noreferrer">{children}</a>:<span>{children}</span>;
    },img:({alt})=><span>{alt}</span>}}>{text.replaceAll('依据工程候选标注的和声解读','和声解读')}</Markdown></div>;
}
