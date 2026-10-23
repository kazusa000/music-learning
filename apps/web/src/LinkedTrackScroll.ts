/** The timeline owns vertical position. The track list is a painted mirror,
 * never a second scroll container that can scroll ahead or feed old events back.
 */
export class LinkedTrackScroll {
  private frame=0;
  private target=0;
  private last=0;
  private written=0;
  private observer:ResizeObserver;
  constructor(private host:HTMLElement,private timeline:HTMLElement,private mirror:HTMLElement){
    this.target=timeline.scrollTop;this.paint();
    host.addEventListener('wheel',this.wheel,{passive:false,capture:true});
    timeline.addEventListener('scroll',this.scrolled,{passive:true});
    this.observer=new ResizeObserver(()=>{this.target=this.clamp(this.target);this.paint();});
    this.observer.observe(timeline);this.observer.observe(mirror);
  }
  get position(){return this.timeline.scrollTop;}
  private clamp(value:number){return Math.max(0,Math.min(this.timeline.scrollHeight-this.timeline.clientHeight,value));}
  private paint(){this.written=this.timeline.scrollTop;this.mirror.style.transform=`translate3d(0,${-this.written}px,0)`;}
  private scrolled=()=>{
    // Wheel frames generate their own asynchronous scroll events. Those events
    // must not reset the remaining wheel target or reverse the other pane.
    if(Math.abs(this.timeline.scrollTop-this.written)>.1){
      this.target=this.timeline.scrollTop;if(this.frame)cancelAnimationFrame(this.frame);this.frame=0;
    }
    this.paint();
  };
  private wheel=(event:WheelEvent)=>{
    if(event.defaultPrevented||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||Math.abs(event.deltaX)>Math.abs(event.deltaY))return;
    const element=event.target as Element;
    if(element.closest('input,select,textarea,[contenteditable=true]'))return;
    if(!this.timeline.contains(element)&&!this.mirror.parentElement?.contains(element))return;
    event.preventDefault();
    const unit=event.deltaMode===1?16:event.deltaMode===2?this.timeline.clientHeight:1;
    this.target=this.clamp((this.frame?this.target:this.position)+event.deltaY*unit);
    if(!this.frame){this.last=performance.now();this.frame=requestAnimationFrame(this.step);}
  };
  private step=(now:number)=>{
    const distance=this.target-this.position,dt=Math.max(1,Math.min(40,now-this.last));this.last=now;
    const next=Math.abs(distance)<.5?this.target:this.position+distance*(1-Math.exp(-dt/32));
    this.timeline.scrollTop=this.clamp(next);this.paint();
    if(Math.abs(this.target-this.position)>.5)this.frame=requestAnimationFrame(this.step);
    else{this.timeline.scrollTop=this.target;this.paint();this.frame=0;}
  };
  setPosition(value:number){
    if(this.frame)cancelAnimationFrame(this.frame);this.frame=0;
    this.target=this.clamp(value);this.timeline.scrollTop=this.target;this.paint();
  }
  close(){
    if(this.frame)cancelAnimationFrame(this.frame);this.frame=0;this.observer.disconnect();
    this.host.removeEventListener('wheel',this.wheel,true);this.timeline.removeEventListener('scroll',this.scrolled);
    this.mirror.style.transform='';
  }
}
