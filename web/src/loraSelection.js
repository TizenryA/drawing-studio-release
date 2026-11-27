// Imported slots must remain in the payload: omission preserves the original graph.
export const setLoraStrength=(lora,strength)=>({...lora,strength,strength_model:strength,strength_clip:strength});

export const matchesLora=(lora,target)=>target.node_id!=null?
 lora.node_id!=null&&String(lora.node_id)===String(target.node_id):lora.node_id==null&&lora.id===target.id;

export function loraPickerRows(models,loras){
 const rows=models.flatMap(model=>{
  const slots=loras.filter(lora=>lora.node_id!=null&&lora.id===model.id);
  return slots.length?[...slots.map(slot=>({...model,node_id:slot.node_id})),
   ...(loras.some(lora=>lora.node_id==null&&lora.id===model.id)?[model]:[])]:[model];
 });
 return [...rows,...loras.filter(lora=>lora.node_id!=null&&!models.some(model=>model.id===lora.id))];
}

export const removeLora=(loras,target)=>loras.flatMap(lora=>lora!==target?[lora]:
 lora.node_id!=null?[{...setLoraStrength(lora,0),enabled:false}]:[]);

export function toggleLora(loras,model){
 const matches=loras.filter(lora=>matchesLora(lora,model));
 if(matches.some(lora=>lora.enabled!==false))return matches.reduce((next,lora)=>removeLora(next,lora),loras);
 const strength=model.recommended_weight??0.8;
 if(matches.length)return loras.map(lora=>matchesLora(lora,model)?{...setLoraStrength(lora,strength),enabled:true}:lora);
 return [...loras,setLoraStrength({id:model.id,name:model.name},strength)];
}
