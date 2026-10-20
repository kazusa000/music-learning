import {useEffect,useState} from 'react';
import {call} from './api';
export function useInstrumentLabels(presetIds:string[],parameterNames:string[]=[]){
  const key=JSON.stringify({preset_ids:[...new Set(presetIds)].sort(),parameter_names:[...new Set(parameterNames)].sort().slice(0,256)});
  const [labels,setLabels]=useState<{presets:Record<string,string>;parameters:Record<string,string>}>({presets:{},parameters:{}});
  useEffect(()=>{let active=true;void call<typeof labels>('instruments.labels',JSON.parse(key)).then(r=>{if(active)setLabels(r);}).catch(()=>{});return()=>{active=false;};},[key]);
  return labels;
}
