#ifndef BFCD2B97_335D_4A22_9232_6A67FA01CAF5
#define BFCD2B97_335D_4A22_9232_6A67FA01CAF5
#define UNUSED(x) (void)(x)
#include <vector>
#include <deque>
#include "Basic.hpp"
#include "WaveletTool.h"

namespace basic
{
    namespace mathtool
    {
        /**
         *  有关小波的算法均参考matlab实现重构,参考wavedec.m
         *
         *  @param x         输入一维信号
         *  @param wname     小波名
         *  @param level     层数
         *
         *  @return 结果vector<vector<T>> = {cA3, cD3, cD2, cD1}
         *
         *  @see wfilters(const std::string &wname,const std::string &o)
         */
        template <typename T>
        std::vector<std::vector<T>> wavedec(const std::vector<T> &x, const std::string &wname, size_t level);

        /**
         *  有关小波的算法均参考matlab实现重构,参考waverec.m
         *
         *  @param c     c
         *  @param wname wname
         *
         *  @return     返回结果
         */
        template <typename T>
        std::vector<T> waverec(const std::vector<std::vector<T>> &c, const std::string &wname);


        namespace WaveletInner
        {

            /**
             *  目前只支持bior3.5,并且是硬编码，采样来自于matlab.有关小波的算法均参考matlab实现重构
             *
             *  @param wname wname
             *  @param o     o
             *
             *  @return pair<Lo_D, Hi_D>
             */
            template <typename T>
            std::pair<std::vector<T>, std::vector<T>> wfilters(const std::string &wname, const std::string &o);

        }

        template <typename T>
        std::vector<std::vector<T>> splitVector(const std::vector<T>& a, const std::vector<int>& b) {
            std::vector<std::vector<T>> result;
            int start = 0;
            for (auto& len : b) {
                if (start + len > a.size()) {
                    break;
                }
                std::vector<T> temp(a.begin() + start, a.begin() + start + len);
                result.push_back(temp);
                start += len;
            }
            return result;
        }

        template <typename T>
        std::vector<T> waverec(const std::vector<std::vector<T>> &cc,
                               const std::string &wname)
        {
            static_assert(std::is_floating_point<T>::value, "T must float type!");
            std::vector<T> c;
            std::vector<size_t> l;
            std::vector<int> L;
            for (const auto &item : cc)
            {
                c.insert(c.end(), item.cbegin(), item.cend());
                l.emplace_back(item.size());
                L.emplace_back((int)item.size());
            }
            int cofLen = 0;
            if ("sym5" == wname)
                cofLen = 10;
            else if ("db4" == wname)
                cofLen = 8;
            else    
                throw std::invalid_argument("waverec not support");
            auto lastSize = (cc.cend() - 1)->size();
            l.emplace_back(lastSize * 2 - cofLen + 2);
            L.emplace_back(lastSize * 2 - cofLen + 2);

            auto charName = wname.c_str();
            auto coefNew = wavelet::WRCoef('a', c, L, charName, 0);
            return coefNew;
        }

        template <typename T>
        std::vector<std::vector<T>> wavedec(const std::vector<T> &x,
                                            const std::string &wname, size_t n)
        {
            static_assert(std::is_floating_point<T>::value, "T must float type!");
            // wname string to const char
            auto charName = wname.c_str();
            auto dec = wavelet::WaveDec(x, n, charName);
            auto resNew = splitVector(dec.C, dec.L);
            return resNew;
        }

        namespace WaveletInner
        {

            template <typename T>
            std::pair<std::vector<T>, std::vector<T>> wfilters(const std::string &wname, const std::string &o)
            {
                static_assert(std::is_floating_point<T>::value, "T must float type!");
                // db4 sym5
                if (wname == "sym5" && o == "d")
                {
                    std::vector<T> Lo_D = {0.027333068345077982,
                                           0.029519490925774643,
                                           -0.039134249302383094,
                                           0.1993975339773936,
                                           0.7234076904024206,
                                           0.6339789634582119,
                                           0.01660210576452232,
                                           -0.17532808990845047,
                                           -0.021101834024758855,
                                           0.019538882735286728};
                    std::vector<T> Hi_D = {-0.019538882735286728,
                                           -0.021101834024758855,
                                           0.17532808990845047,
                                           0.01660210576452232,
                                           -0.6339789634582119,
                                           0.7234076904024206,
                                           -0.1993975339773936,
                                           -0.039134249302383094,
                                           -0.029519490925774643,
                                           0.027333068345077982};
                    return std::make_pair(Lo_D, Hi_D);
                }
                if (wname == "sym5" && o == "r")
                {
                    std::vector<T> Lo_D = {0.019538882735286728,
                                           -0.021101834024758855,
                                           -0.17532808990845047,
                                           0.01660210576452232,
                                           0.6339789634582119,
                                           0.7234076904024206,
                                           0.1993975339773936,
                                           -0.039134249302383094,
                                           0.029519490925774643,
                                           0.027333068345077982};

                    std::vector<T> Hi_D = {0.027333068345077982,
                                           -0.029519490925774643,
                                           -0.039134249302383094,
                                           -0.1993975339773936,
                                           0.7234076904024206,
                                           -0.6339789634582119,
                                           0.01660210576452232,
                                           0.17532808990845047,
                                           -0.021101834024758855,
                                           -0.019538882735286728};
                    return std::make_pair(Lo_D, Hi_D);
                }
                if (wname == "db4" && o == "d")
                {
                    std::vector<T> Lo_D = {-0.010597401785069032,
                                           0.0328830116668852,
                                           0.030841381835560764,
                                           -0.18703481171909309,
                                           -0.027983769416859854,
                                           0.6308807679298589,
                                           0.7148465705529157,
                                           0.2303778133088965};
                    std::vector<T> Hi_D = {-0.2303778133088965,
                                           0.7148465705529157,
                                           -0.6308807679298589,
                                           -0.027983769416859854,
                                           0.18703481171909309,
                                           0.030841381835560764,
                                           -0.0328830116668852,
                                           -0.010597401785069032};
                    return std::make_pair(Lo_D, Hi_D);
                }
                if (wname == "db4" && o == "r")
                {
                    std::vector<T> Lo_D = {0.2303778133088965,
                                           0.7148465705529157,
                                           0.6308807679298589,
                                           -0.027983769416859854,
                                           -0.18703481171909309,
                                           0.030841381835560764,
                                           0.0328830116668852,
                                           -0.010597401785069032};

                    std::vector<T> Hi_D = {-0.010597401785069032,
                                           -0.0328830116668852,
                                           0.030841381835560764,
                                           0.18703481171909309,
                                           -0.027983769416859854,
                                           -0.6308807679298589,
                                           0.7148465705529157,
                                           -0.2303778133088965};
                    return std::make_pair(Lo_D, Hi_D);
                }
                throw std::invalid_argument("wfilters only support bior3.5");
            }


/***************************************优化*************************************************/


        }
    } // namespace mathtool
} // namespace basic

#endif /* BFCD2B97_335D_4A22_9232_6A67FA01CAF5 */

