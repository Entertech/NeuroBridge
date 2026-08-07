#ifndef A16F66A9_FF08_4080_B667_8E7E69823B03
#define A16F66A9_FF08_4080_B667_8E7E69823B03

#define ENABLE_DEBUG false
#include <vector>
#include <iostream>
typedef std::vector<int> vectori;
typedef std::vector<double> vectord;

template <typename T>
void logValue(const T &t, const std::string& name)
{
#if ENABLE_DEBUG
    std::cout<<name<<"*";
    std::cout<<t<<std::endl;
#endif
}

template <typename T>
void logArray(const std::vector<T> &t, const std::string& name)
{

#if ENABLE_DEBUG
    std::cout<<name;
    std::cout<<"-------"<<std::endl;
    std::cout<<"size: - "<<t.size()<<std::endl;
    for (auto &e: t)
    {
        std::cout<<e<<" ";
    }
    std::cout<<"|"<<std::endl;
#endif
}

#endif /* A16F66A9_FF08_4080_B667_8E7E69823B03 */
