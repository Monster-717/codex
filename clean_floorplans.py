"""Remove perimeter dimensions and OCR-detected room areas without redrawing walls."""
import argparse
import json
from pathlib import Path
import re
import zipfile
import cv2
import numpy as np


def area_text(text):
    text = text.replace(' ', '').replace('，', '.').replace(',', '.')
    return bool(re.fullmatch(r'\d+(?:\.\d+)?(?:m|M|㎡)[²2?~]?', text))


def floor_mask(image):
    # These drawings encode room fills with pastel colours; dimensions are neutral grey.
    spread = image.max(axis=2).astype(int)-image.min(axis=2).astype(int)
    coloured = ((spread >= 9) & (image.max(axis=2) > 140)).astype('uint8')
    coloured = cv2.morphologyEx(coloured, cv2.MORPH_CLOSE, np.ones((13,13),np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(coloured)
    room = np.zeros(coloured.shape, np.uint8)
    for i in range(1,n):
        if stats[i,cv2.CC_STAT_AREA] >= 250:
            room[labels==i]=1
    if room.sum() < 3000:
        raise ValueError('No reliable pastel floor footprint detected; requires manual masking')
    # Include the outside half of boundary walls and window frames.
    keep = cv2.dilate(room, np.ones((17,17),np.uint8))
    grey = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    n, labels, stats, _ = cv2.connectedComponentsWithStats((grey<225).astype('uint8'))
    touching = np.unique(labels[keep>0])
    for i in touching:
        if i and stats[i,cv2.CC_STAT_AREA] >= 3:
            keep[labels==i]=1
    # Keep the anti-aliased edges of attached entrance doors and windows.
    return cv2.dilate(keep,np.ones((3,3),np.uint8))


def clean(image, ocr):
    keep = floor_mask(image)
    grey = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    dark=(grey<210).astype('uint8')
    horizontal=cv2.morphologyEx(dark,cv2.MORPH_OPEN,np.ones((1,31),np.uint8))
    vertical=cv2.morphologyEx(dark,cv2.MORPH_OPEN,np.ones((31,1),np.uint8))
    structural=horizontal|vertical
    protected=np.zeros(grey.shape,np.uint8)
    all_text=np.zeros(grey.shape,np.uint8)
    for box,text,score in ocr:
        pts=np.array(box,dtype=np.int32)
        cv2.fillPoly(all_text,[pts],1)
        if not area_text(text):
            cv2.fillPoly(protected,[pts],1)
    all_text=cv2.dilate(all_text,np.ones((5,5),np.uint8))
    spread=image.max(axis=2).astype(int)-image.min(axis=2).astype(int)
    valid=(spread>=9)&(grey>170)&(all_text==0)&(structural==0)&(keep>0)
    if not valid.any():
        raise ValueError('No reliable background pixels to reconstruct text regions')
    _,nearest=cv2.distanceTransformWithLabels((~valid).astype('uint8'),cv2.DIST_L2,5,
                                              labelType=cv2.DIST_LABEL_PIXEL)
    palette=np.zeros((int(nearest.max())+1,3),np.uint8)
    palette[nearest[valid]]=image[valid]
    mask=np.zeros(grey.shape,np.uint8)
    result=image.copy()
    areas,review=[],[]
    for box,text,score in ocr:
        if not area_text(text):
            continue
        points=np.array(box,dtype=np.int32)
        polygon=np.zeros(grey.shape,np.uint8)
        cv2.fillPoly(polygon,[points],1)
        polygon=cv2.dilate(polygon,np.ones((5,5),np.uint8))
        overlap=int(np.count_nonzero((polygon>0)&(structural>0)))
        polygon[(structural>0)|(protected>0)|(keep==0)]=0
        mask[polygon>0]=255
        # Use a robust local fill colour rather than propagating text halos or crossing wall colours.
        x0,y0=points.min(axis=0);x1,y1=points.max(axis=0)
        x0=max(0,x0-14);y0=max(0,y0-14)
        x1=min(image.shape[1],x1+15);y1=min(image.shape[0],y1+15)
        local=image[y0:y1,x0:x1][valid[y0:y1,x0:x1]]
        if len(local):
            quant=local//16
            colours,counts=np.unique(quant,axis=0,return_counts=True)
            cluster=local[np.all(quant==colours[counts.argmax()],axis=1)]
            colour=np.median(cluster,axis=0).astype('uint8')
            result[polygon>0]=colour
        else:
            result[polygon>0]=palette[nearest[polygon>0]]
        areas.append({'text':text,'confidence':score,'box':box})
        if score<.8 or overlap:
            review.append({'text':text,'reason':'low confidence or adjacent architectural line',
                           'box':box,'protected_line_pixels':overlap})
    result[keep==0]=255
    changed=np.any(result!=image,axis=2)
    return result,mask,{'area_labels':areas,'needs_review':review,
                        'area_mask_pixels':int(np.count_nonzero(mask)),
                        'changed_pixels':int(changed.sum()),
                        'protected_line_changes':int(np.count_nonzero(changed&(structural>0)&(keep>0))),
                        'protected_text_changes':int(np.count_nonzero(changed&(protected>0)&(keep>0))),
                        'exterior_whitened_pixels':int(np.count_nonzero((keep==0)&(grey<240)))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--ocr-cache',help='Optional cache of RapidOCR JSON results, one per image stem')
    args=parser.parse_args()
    source=Path(args.input).resolve();out=Path(args.output).resolve()
    if source==out or source in out.parents:
        parser.error('Output must be separate from the input directory')
    for folder in ['images','masks']:(out/folder).mkdir(parents=True,exist_ok=True)
    cache=Path(args.ocr_cache) if args.ocr_cache else out/'ocr'
    cache.mkdir(parents=True,exist_ok=True)
    engine=None;report=[]
    files=sorted(p for p in source.rglob('*') if p.suffix.lower() in ['.jpg','.jpeg','.png','.webp'])
    for index,p in enumerate(files,1):
        image=cv2.imdecode(np.frombuffer(p.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
        if image is None:
            report.append({'source':str(p.relative_to(source)),'error':'Cannot decode image'})
            continue
        record=cache/(p.stem+'.json')
        if record.exists():
            ocr=json.loads(record.read_text(encoding='utf-8'))
        else:
            if engine is None:
                from rapidocr_onnxruntime import RapidOCR
                engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=2)
            ocr,_=engine(image)
            ocr=ocr or []
            record.write_text(json.dumps(ocr,ensure_ascii=False),encoding='utf-8')
        try:
            result,mask,info=clean(image,ocr)
            relative=p.relative_to(source).with_suffix('.png')
            for folder,data in [('images',result),('masks',mask)]:
                dest=out/folder/relative;dest.parent.mkdir(parents=True,exist_ok=True)
                ok,encoded=cv2.imencode('.png',data)
                if not ok:raise RuntimeError('PNG encoding failed')
                dest.write_bytes(encoded.tobytes())
            report.append({'source':str(p.relative_to(source)),'output':str(relative),
                           'dimensions':[image.shape[1],image.shape[0]],**info})
        except ValueError as error:
            report.append({'source':str(p.relative_to(source)),'error':str(error)})
        if index%10==0 or index==len(files):print(f'Processed {index}/{len(files)}',flush=True)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'README.txt').write_text('images/: cleaned PNGs. masks/: white pixels mark area-text background replacement. report.json: detected text, uncertainty flags and changed pixel counts. Perimeter dimensions are removed using a pastel-floor footprint, retaining attached doors and windows. This method is tailored to these coloured Fang plans. Original files were not changed. Flagged labels can overlap architectural lines and require human checking. No generative redrawing was used.\n',encoding='utf-8')
    with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
        for p in out.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(out.parent))
    print('ZIP:',out.with_suffix('.zip'),flush=True)


if __name__=='__main__':main()
