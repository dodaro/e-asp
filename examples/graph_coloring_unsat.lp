{ color(T,C) : col(C)}  :- v(T).
:- color(V1,C),  color(V2,C), edge(V1, V2).
:- v(T), #count{C : color(T,C)} != 1.
col(green).
col(blue).
col(red).
v(1).
v(2).
v(3).
v(4).
edge(1,2).
edge(1,3).
edge(1,4).
edge(2,1).
edge(2,3).
edge(2,4).
edge(3,1).
edge(3,2).
edge(3,4).
edge(4,1).
edge(4,2).
edge(4,3).