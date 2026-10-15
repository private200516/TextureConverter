"""Read-only representative corpus conversion and independent FO4 reader validation."""
import argparse,hashlib,io,json,struct,sys,time
from pathlib import Path
from material_codec import parse,serialize,read_material_bytes
from material_converter import convert_material

def independent_fo4(data,pynifly_dir):
    sys.path.insert(0,str(pynifly_dir))
    from pyn.bgsmaterial import BGSMaterial,BGEMaterial
    class StrictFields:
        def read_field(self,name,t):
            if hasattr(t,'_length_'):
                fmt=str(t._length_)+'s' if t._type_._type_=='c' else t._type_._type_*t._length_
            else:fmt=t._type_
            values=struct.unpack('<'+fmt,self.sourcefile.read(struct.calcsize('<'+fmt)))
            setattr(self,name,values[0] if len(values)==1 else values)
        def read_text(self,name,condition=True):
            if condition:
                n=struct.unpack('<I',self.sourcefile.read(4))[0]
                b=self.sourcefile.read(n)
                if len(b)!=n:raise ValueError('Independent reader string truncation')
                value=b.rstrip(b'\0').decode('utf8',errors='surrogateescape')
                if value:self.textures[name]=value
    class S(StrictFields,BGSMaterial):pass
    class E(StrictFields,BGEMaterial):pass
    m=(S if data[:4]==b'BGSM' else E)();stream=io.BytesIO(data);m._read(stream)
    if stream.tell()!=len(data):raise ValueError('Independent reader did not consume exact file')
    if m.version!=2:raise ValueError('Expected independent FO4 v2')
    return {'reader':'PyNifly MaterialFile with strict EOF/field bounds','version':m.version,'bytes_consumed':stream.tell(),'textures':m.textures}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--inventory',required=True);parser.add_argument('--output',required=True);parser.add_argument('--direction',default='76-to-4');parser.add_argument('--pynifly-dir')
    args=parser.parse_args();inventory=json.loads(Path(args.inventory).read_text());out=Path(args.output);out.mkdir(parents=True,exist_ok=False);results=[]
    for index,(feature,path) in enumerate(inventory['features'].items()):
        source=Path(path)
        try:
            data=read_material_bytes(source);h=hashlib.sha256(data).hexdigest()
            material,info=convert_material(data,args.direction);encoded=serialize(material);target=out/'converted'/f'{index:04d}_{source.name}';target.parent.mkdir(exist_ok=True);target.write_bytes(encoded);check=parse(target)
            independent=independent_fo4(encoded,args.pynifly_dir) if args.pynifly_dir and args.direction=='76-to-4' else None
            if hashlib.sha256(read_material_bytes(source)).hexdigest()!=h:raise ValueError('Input changed')
            results.append(dict(source=str(source),target=str(target),status='validated',feature=feature,source_hash=h,target_hash=hashlib.sha256(encoded).hexdigest(),independent=independent,info=info))
        except Exception as e:results.append(dict(source=str(source),status='failed',error=str(e)))
    summary=dict(total=len(results),passed=sum(x['status']=='validated' for x in results),failed=sum(x['status']=='failed' for x in results),independent_reader_count=sum(bool(x.get('independent')) for x in results),game_verified=False,results=results)
    (out/'report.json').write_text(json.dumps(summary,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in summary.items() if k!='results'}));return bool(summary['failed'])
if __name__=='__main__':raise SystemExit(main())
