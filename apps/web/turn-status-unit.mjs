import assert from 'node:assert/strict';
import {turnStatus} from './src/turnStatus.ts';

const turn={status:'succeeded',context:{},tools:[]};
assert.equal(turnStatus(turn,[]),'回复结束');
const failed={...turn,context:{preset_captures:['c']}};
assert.equal(turnStatus(failed,[{id:'c',status:'failed'}]),'音色未更换');
assert.equal(turnStatus({...failed,context:{...failed.context,arrangement:{revision_id:'r'}}},[{id:'c',status:'failed'}]),'工程已更新');
assert.equal(turnStatus({...turn,tools:[{tool:'music_edit',success:false}]},[]),'修改未完成');
assert.equal(turnStatus({...turn,status:'running'},[]),'正在处理');
console.log(JSON.stringify({status:'succeeded',coverage:'reply-end/failed-capture/committed-receipt/edit-failure/running'}));
