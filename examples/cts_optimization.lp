x(RID,DAY,TS) : mss(DAY,TS) :- reg(RID,_).
preparation_time(RID,DUR) :- reg(RID,_), DUR = #sum{D,PH: duration(RID,PH,D), PH != 4}.
:- x(RID,_,TS), reg(RID,_), preparation_time(RID,T), TS-T < 1.
patient_seat_day(ID,T,RID,DAY) : seat(ID,T) :- x(RID, DAY, _).
patient_seat_ts(ID,T,RID,DAY,TS..TS+DUR-1) :- patient_seat_day(ID,T,RID,DAY), x(RID,DAY,TS), duration(RID,4,DUR), DUR > 0.
:- #count{RID: patient_seat_ts(ID,T,RID,DAY,TS)} > 1, seat(ID,T), mss(DAY, TS).
:~ patient_seat_day(ID,T1,RID,DAY), reg(RID, T2), T1 != T2. [1@1,RID]

reg("pat1","bed").
reg("pat2","chair").
reg("pat3","chair"). 
mss(1,1).
mss(1,2).
mss(1,3).
mss(1,4).
mss(1,5).
mss(1,6).
mss(1,7).
mss(1,8).
mss(1,9).
duration("pat1",4,5). 
duration("pat1",1,2).
duration("pat2",4,3). 
duration("pat2",3,1). 
duration("pat2",1,2).
duration("pat3",4,4). 
duration("pat3",3,2). 
duration("pat3",1,2).
seat("chair1","chair"). 
seat("chair2","chair").