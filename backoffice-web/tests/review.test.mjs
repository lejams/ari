import assert from 'node:assert/strict';
import {clone,pointsFor,resolvePoint,validateDraft,markedRanges,uncoveredPassages,sortedQueue,compareFirst} from '../review.mjs';
import {ProtocolReview} from '../protocol-review.mjs';
import {blockers} from '../api.mjs';

const draft = () => ({source:{title_hint:'Case',segment_index:0},location:{land:'Bayern'},patient:{age_years:40,sex:'male',presenting_complaint_de:'Pain',uncertainty:null},diagnosis:{suspected_de:null,differentials_de:[],uncertainty:null},arzt_arzt:{presentation_summary_de:null,discussion_points_de:[],uncertainty:null},outcome:{result:'unknown',examiner_feedback_de:[],candidate_tips_de:[],uncertainty:null},anamnesis:[{id:'a01',label_de:'Medication',value_de:null,polarity:'unknown',source_pages:[],uncertainty:'Not reported',quote_de:null}],examiner_questions:[],fachbegriffe:[],unresolved_questions:[{id:'u01',text_fr:'Reported?',critical:true,answer:null}],field_uncertainties:[{path:'outcome.result',reason_fr:'Not reported',resolution:null}],pedagogy:{difficulty:null,notes_fr:null,critical_pitfalls:[],pitfalls:[]},pseudonymisation:{removed_kinds:[],notes:[]}});
{
 const record=draft(), initial=clone(record), points=pointsFor(record,'doctor',initial);
 assert.equal(points.filter(p=>!p.resolved).length,blockers(record,'doctor').length);
 const unknown=points.find(p=>p.kind==='anamnesis');
 resolvePoint(record,unknown,{choice:'unknown'});
 assert.equal(record.anamnesis[0].value_de,null);assert.equal(record.anamnesis[0].polarity,'unknown');
 resolvePoint(record,points.find(p=>p.kind==='question'),{value:'The source does not report it.'});
 resolvePoint(record,points.find(p=>p.kind==='field'),{value:'unknown',resolution:'Not stated in PDF'});
 resolvePoint(record,points.find(p=>p.id==='difficulty'),{value:'mittel'});
 validateDraft(record); assert.deepEqual(blockers(record,'doctor'),[]);
 assert.equal(pointsFor(record,'doctor',initial).filter(p=>p.resolved).length,4,'resolved chips remain visible and count is real');
 assert.throws(()=>resolvePoint(draft(),unknown,{choice:'corrected',value:''}),/Renseignez/);
 assert.throws(()=>resolvePoint(draft(),unknown,{choice:'corrected',value:'No medication'}),/présente ou absente/);
 const corrected=draft(); resolvePoint(corrected,unknown,{choice:'corrected',value:'No medication',polarity:'absent'}); assert.equal(corrected.anamnesis[0].polarity,'absent');
 const kept=draft(); kept.anamnesis[0].polarity='absent'; resolvePoint(kept,unknown,{choice:'kept',value:'No medication'}); assert.equal(kept.anamnesis[0].polarity,'absent');
 const invalid=draft(); invalid.anamnesis[0].value_de='Invented';assert.throws(()=>validateDraft(invalid),/inconnue/);
 assert.ok(compareFirst(initial,record).some(change=>change.path==='pedagogy.difficulty'));
}
{
 const record=draft();record.anamnesis[0]={id:'a01',value_de:'Covered',polarity:'present',source_pages:[1]};
 const text='😀 context\nSTART\nCovered\nUnmapped sentence.\nNEXT protocol\n';
 const chars=Array.from(text), page={page_number:1,text,start:chars.join('').indexOf('START')-1,end:Array.from(text.slice(0,text.indexOf('NEXT'))).length};
 const marks=markedRanges(page,record);
 assert.equal(chars.slice(marks[0].start,marks[0].end).join(''),'Covered','offsets match Python code points despite emoji');
 const free=uncoveredPassages([page],record);
 assert.ok(free.some(p=>p.text==='Unmapped sentence.')); assert.ok(!free.some(p=>p.text.includes('NEXT') || p.text.includes('context')));
 assert.equal(free.find(p=>p.text==='Unmapped sentence.').id,`1:${Array.from(text.slice(0,text.indexOf('Unmapped'))).length}:${Array.from(text.slice(0,text.indexOf('Unmapped'))).length+18}`);
 assert.deepEqual(sortedQueue([{id:'long',source_text_length:120},{id:'b',source_text_length:12},{id:'a',source_text_length:12}]).map(p=>p.id),['a','b','long']);
}
// Exercise the actual controller's persistence with a minimal DOM, independent of rendering.
const elements=new Map();
const element=id=>elements.get(id) || (elements.set(id,{value:'',hidden:true,dataset:{},addEventListener(){},classList:{toggle(){}},getAttribute(){return 'false';}}),elements.get(id));
globalThis.document={getElementById:element};globalThis.window={addEventListener(){},confirm:()=>false};globalThis.location={hash:'#/protocols/P'};
function controller(client) {
 const r=draft();r.anamnesis[0].uncertainty=null;r.unresolved_questions[0].answer='Not reported';r.field_uncertainties[0].resolution='Verified';r.pedagogy.difficulty='mittel';
 const c=new ProtocolReview({client,lands:()=>['Bayern'],perform:action=>action(),notice(){},view(){}});
 Object.assign(c,{active:true,hash:'#/protocols/P',stage:'doctor',protocol:{id:'P',status:'doctor_review',version:1,content_hash:'a'.repeat(64),record:clone(r)},record:r,initial:clone(r)});
 c.render=c.update=()=>{};c.record.pedagogy.notes_fr='Reviewed source';return c;
}
{
 const calls=[];let accountRoles=['physician_reviewer'];
 const client={has:(...roles)=>roles.some(role=>accountRoles.includes(role)),revise:async(id,version,hash,record)=>{calls.push(['save',version,hash]);return {id,status:'doctor_review',version:2,content_hash:'b'.repeat(64),record:clone(record)};},decide:async(id,stage,body)=>{calls.push(['decide',stage,body]);return {id,status:'doctor_approved',version:2,content_hash:'b'.repeat(64),record:clone(c.record)};},protocols:async()=>({items:[]})};
 const c=controller(client);element('decision-notes').value='Reviewed';await c.decide('approve');
 assert.deepEqual(calls.map(call=>call[0]),['save','decide']);assert.equal(calls[1][2].version,2);assert.equal(calls[1][2].protocol_hash,'b'.repeat(64));assert.equal(element('decision-notes').value,'');assert.equal(location.hash,'#/review');
 accountRoles=['owner'];c.protocol.status='doctor_review';assert.equal(c.canDecide(),false,'owner cannot act as doctor');
 c.protocol.status='gold';assert.equal(c.canRevise(),false);
}
{
 const c=controller({has:()=>true,revise:async()=>{throw new Error('Version conflict');}});
 await assert.rejects(()=>c.save(),/Version conflict/);assert.equal(c.protocol.version,1);assert.equal(c.record.pedagogy.notes_fr,'Reviewed source');assert.equal(c.dirty(),true);
 c.pointDirty=true;await assert.rejects(()=>c.save(),/point en cours/);assert.equal(c.confirmLeave(),false);
}
{
 const c=controller({has:()=>true,revise:async(id,v,h,r)=>({id,status:'doctor_review',version:2,content_hash:'b'.repeat(64),record:clone(r)}),decide:async()=>{throw new Error('Decision failed');}});
 element('decision-notes').value='Corrections required';await assert.rejects(()=>c.decide('request_changes'),/Decision failed/);
 assert.equal(c.protocol.version,2);assert.equal(element('decision-notes').value,'Corrections required');assert.equal(c.record.pedagogy.notes_fr,'Reviewed source');assert.equal(c.unsaved(),true);
 assert.equal(c.removalBlocked('unresolved_questions',{critical:true,answer:null}),true);
}
console.log('Review: blockers, explicit unknown resolution, source Unicode boundaries, queue, fresh hash decisions and failure recovery passed');
