//
// Created by 雷京颢 on 2018/7/18.
//

#ifndef ENTEREEG_NUMPY_BASIC_H
#define ENTEREEG_NUMPY_BASIC_H

#include <algorithm>
#include <complex>
#include <vector>
#include <deque>
#include <numeric>
#include <stdexcept>
#include <functional>
#include <math.h>
namespace basic
{
    namespace mathtool
    {

        /**
         *  用于实数类型的求平均，参数必须包含一个以上的数据！注意内部会将T类型转换成long double累加！因此需要保证从T类型到long double转换的存在
         *
         *  @param data 待求数据的vector
         *
         *  @return 数据平均值
         */
        template <typename T>
        T mean(const std::vector<T> &data)
        {
            static_assert(std::is_floating_point<T>::value ||
                              std::is_integral<T>::value,
                          "T must float/int type!");
            if (data.empty())
                throw std::invalid_argument("data must be not empty.");
            long double total = *data.begin();
            for (auto it = data.cbegin() + 1; it != data.cend(); ++it)
                total += (*it);
            return static_cast<T>(total / data.size());
        }

        template <typename T>
        size_t round(const T data)
        {
            size_t lNum = (size_t)(data + 0.5);
            return lNum;
        }

        template <typename T>
        std::vector<T> abs(const std::vector<std::complex<T>> &data)
        {
            std::vector<T> result;
            result.reserve(data.size());
            std::for_each(data.cbegin(), data.cend(), [&](const std::complex<T> &input)
                          { result.emplace_back(std::abs(input)); });
            return result;
        }

        template <typename T>
        std::vector<T> abs(const std::vector<T> &data)
        {
            std::vector<T> result;
            result.reserve(data.size());
            std::for_each(data.cbegin(), data.cend(), [&](const T &input)
                          { result.emplace_back(std::abs(input)); });
            return result;
        }

        template <typename T>
        std::vector<T> log(const std::vector<T> &data)
        {
            std::vector<T> result;
            result.reserve(data.size());
            std::for_each(data.cbegin(), data.cend(), [&](const T &input)
                          { result.emplace_back(std::log(input)); });
            return result;
        }

        /**
         *  模拟matlab内截取数组内一段数据的操作。
         *
         *  @param data   原始数组vector
         *  @param start  与python一致的截取开始下标
         *  @param end    与python一致的截取截取下标
         *
         *  @return  截取结果vector
         */
        template <typename T>
        std::vector<T> truncate(const std::vector<T> &data, int start, int endIndex)
        {
            const int dataSize = static_cast<int>(data.size());

            if (start < 0)
                start += dataSize;
            if (endIndex < 0)
                endIndex += dataSize;
            if (start >= endIndex || start == dataSize)
                return std::vector<T>();
            endIndex = std::min(endIndex, dataSize);
            std::vector<T> result;
            result.reserve(endIndex - start); // Reserve space
            result.assign(data.begin() + start, data.begin() + endIndex);
            return result;
        }

        /**
         *  模拟matlab内截取数组内数据操作。支持负数下标志
         *
         *  @param data   原始数组vector
         *  @param index  与python一致的截取下标
         *
         *  @return  截取结果
         */
        template <typename T>
        T truncate(const std::vector<T> &data, int index)
        {
            if (index >= 0)
                return data[index];
            else
                return data[data.size() + index];
        }

        /**
         *  用于各种数据类型的求最大值
         *
         *  @param data 待求数据的vector
         *
         *  @return 数据最大值
         */
        template <typename T>
        T max(const std::vector<T> &data)
        {
            if (data.empty())
                throw std::invalid_argument("data must be not empty.");
            T max = data[0];
            for (auto it = data.cbegin(); it != data.cend(); ++it)
            {
                if ((*it) > max)
                    max = *it;
            }
            return max;
        }

        /**
         *  用于各种数据类型的求最小值
         *
         *  @param data 待求数据的vector
         *
         *  @return 数据最小值
         */
        template <typename T>
        T min(const std::vector<T> &data)
        {
            if (data.empty())
                throw std::invalid_argument("data must be not empty.");
            T min = data[0];
            for (auto it = data.cbegin(); it != data.cend(); ++it)
            {
                if ((*it) < min)
                    min = *it;
            }
            return min;
        }

        /**
         *  求和。
         *
         *  @param data 待求数据的vector
         *
         *  @return 数据和
         */
        template <typename T>
        T sum(const std::vector<T> &data)
        {
            static_assert(std::is_floating_point<T>::value ||
                              std::is_integral<T>::value,
                          "T must float/int type!");
            if (data.empty())
                return 0;
            return std::accumulate(data.cbegin() + 1, data.cend(), data[0]);
        }

        /**
         *  求和。
         *
         *  @param data 待求数据的vector
         *
         *  @return 数据和
         */
        template <typename T>
        T sum(const std::deque<T> &data)
        {
            static_assert(std::is_floating_point<T>::value ||
                              std::is_integral<T>::value,
                          "T must float/int type!");
            if (data.empty())
                return 0;
            return std::accumulate(data.cbegin() + 1, data.cend(), data[0]);
        }

        /**
         *  模拟numpy中的数组相减 res=data1-data2
         *
         * @param data1  被减数
         * @param data2  减数
         *
         * @return 数组相减结果
         */
        template <typename T>
        std::vector<T> minus(const std::vector<T> &data1, const std::vector<T> &data2)
        {
            std::vector<T> res;
            if (data1.size() != data2.size())
                throw std::invalid_argument("data1 data2 size not equal.");
            res.reserve(data1.size());
            for (auto it1 = data1.cbegin(), it2 = data2.cbegin(); it1 != data1.cend(); it1++, it2++)
            {
                res.emplace_back((*it1) - (*it2));
            }
            return res;
        }

        /**
         *  模拟numpy中的数组标量相减 res=data1-data2
         *
         * @param data1  被减数数组
         * @param data2  减数标量
         *
         * @return 相减结果
         */
        template <typename T>
        std::vector<T> minus(const std::vector<T> &data1, const T &data2)
        {
            std::vector<T> res;
            res.reserve(data1.size());
            std::for_each(data1.cbegin(), data1.cend(), [&](const T &input)
                          { res.emplace_back(input - data2); });
            return res;
        }

        /**
         *  模拟numpy中的数组相加 res=data1+data2
         *
         * @param data1  被加数
         * @param data2  加数
         *
         * @return 相加结果
         */
        template <typename T>
        std::vector<T> add(const std::vector<T> &data1, const std::vector<T> &data2)
        {
            std::vector<T> res;
            if (data1.size() != data2.size())
                throw std::invalid_argument("data1 data2 size not equal.");
            res.reserve(data1.size());
            for (auto it1 = data1.cbegin(), it2 = data2.cbegin(); it1 != data1.cend(); it1++, it2++)
            {
                res.emplace_back((*it1) + (*it2));
            }
            return res;
        }

        /**
         *  模拟numpy中的数组标量相加 res=data1+data2
         *
         * @param data1  被加数数组
         * @param data2  加数标量
         *
         * @return 相加结果
         */
        template <typename T>
        std::vector<T> add(const std::vector<T> &data1, const T &data2)
        {
            std::vector<T> res;
            res.reserve(data1.size());
            std::for_each(data1.cbegin(), data1.cend(), [&](const T &input)
                          { res.emplace_back(input + data2); });
            return res;
        }

        /**
         * 模拟numpy中的点乘 res = data1*data2
         *
         * @tparam T 任意支持乘法的量
         * @param data1 data1
         * @param data2 data2
         *
         * @return 点乘结果
         */
        template <typename T>
        std::vector<T> dotMultiply(const std::vector<T> &data1, const std::vector<T> &data2)
        {
            std::vector<T> res;
            if (data1.size() != data2.size())
                throw std::out_of_range("data1 data2 size not equal.");
            res.reserve(data1.size());
            for (auto it1 = data1.cbegin(), it2 = data2.cbegin(); it1 != data1.cend(); it1++, it2++)
            {
                res.emplace_back((*it1) * (*it2));
            }
            return res;
        }

        /**
         * 模拟numpy中的点乘 sum(res) = data1*data2
         *
         * @tparam T 任意支持乘法的量
         * @param data1 data1
         * @param data2 data2
         *
         * @return 点乘结果的和
         */
        template <typename T>
        T dotMultiplySum(const std::vector<T> &data1, const std::vector<T> &data2)
        {
            std::vector<T> res;
            if (data1.size() != data2.size())
                throw std::out_of_range("data1 data2 size not equal.");
            res.reserve(data1.size());
            for (auto it1 = data1.cbegin(), it2 = data2.cbegin(); it1 != data1.cend(); it1++, it2++)
            {
                res.emplace_back((*it1) * (*it2));
            }
            auto tmp = sum(res);
            return tmp;
        }

        /**
         * 模拟numpy中的数组标量相乘 res=data1*data2
         * @tparam T     任意支持乘法的量
         * @param data1  数组
         * @param data2  标量
         * @return 相乘结果
         */
        template <typename T>
        std::vector<T> multiply(const std::vector<T> &data1, const T &data2)
        {
            std::vector<T> res;
            res.reserve(data1.size());
            std::for_each(data1.cbegin(), data1.cend(), [&](const T &input)
                          { res.emplace_back(input * data2); });
            return res;
        }

        /**
         * 模拟numpy中的数组标量相除 res=data1/data2
         * @tparam T     任意支持乘法的量
         * @param data1  数组
         * @param data2  标量
         * @return 相乘结果
         */
        template <typename T>
        std::vector<T> divide(const std::vector<T> &data1, const T &data2)
        {
            std::vector<T> res;
            res.reserve(data1.size());
            std::for_each(data1.cbegin(), data1.cend(), [&](const T &input)
                          { res.emplace_back(input / data2); });
            return res;
        }

        /**
         *  将各类实数（浮点数和整数）转换为复数
         *
         *  @param data   实数
         *
         *  @return  复数
         */
        template <typename T>
        std::vector<std::complex<T>> toComplex(const std::vector<T> &data)
        {
            std::vector<std::complex<T>> result;
            for (auto it = data.cbegin(); it != data.cend(); it++)
            {
                result.emplace_back(std::complex<T>(*it, 0.0));
            }
            return result;
        }

        /**
         *  将实数和纯虚数合成为复数
         *
         *  @param real   实数
         *  @param img    纯虚数
         *
         *  @return  复数
         */
        template <typename T>
        std::vector<std::complex<T>> toComplex(const std::vector<T> &real, const std::vector<T> &img)
        {
            if (real.size() != img.size())
                throw std::invalid_argument("real size and img size not equal");
            std::vector<std::complex<T>> result;
            for (auto it1 = real.cbegin(), it2 = img.cbegin(); it1 != real.cend(); it1++, it2++)
            {
                result.emplace_back(std::complex<T>(*it1, *it2));
            }
            return result;
        }

        /**
         *  取复数的实数部分
         *
         *  @param data 需要操作的数据
         *
         *  @return 实数结果
         */
        template <typename T>
        std::vector<T> real(const std::vector<std::complex<T>> &data)
        {
            std::vector<T> res;
            res.reserve(data.size());
            for (auto it = data.cbegin(); it != data.cend(); it++)
                res.emplace_back((*it).real());
            return res;
        }

        /**
         *  模拟python中形如 data[startIndex:endIndex] = elements 的赋值
         *  @tparam T         任意类型
         *  @param data       待赋值数组
         *  @param startIndex 开始下标
         *  @param endIndex   结束下标志
         *  @param elements   待插入数组
         */
        template <typename T>
        void set(std::vector<T> &data, int startIndex, int endIndex, const std::vector<T> &elements)
        {
            if (startIndex < 0)
                throw std::invalid_argument("startIndex should larger than 0");
            if (endIndex < startIndex)
                throw std::invalid_argument("startIndex should >= endIndex");
            if (endIndex > data.size())
                throw std::out_of_range("endIndex should <= data.size()");
            auto latterPart = truncate(data, endIndex, static_cast<int>(data.size()));
            data.resize(static_cast<size_t>(startIndex));
            data.insert(data.end(), elements.begin(), elements.end());
            data.insert(data.end(), latterPart.begin(), latterPart.end());
        }

        template <typename T>
        std::vector<T> truncateMatlab(const std::vector<T> &data, size_t start, typename std::vector<T>::difference_type step, size_t end)
        {
            const size_t dataSize = data.size();
            if (0 == step)
                throw std::invalid_argument("step should not be 0");
            if (start <= 0 || start > dataSize)
                throw std::out_of_range("start index out of range");
            if (end <= 0 || end > dataSize)
                throw std::out_of_range("end index out of range");
            if ((step > 0 && start > end) || (step < 0 && start < end))
                throw std::out_of_range("step and index range mismatch");
            std::vector<T> result;
            result.reserve((end - start) / step + 1);  // Reserve space in the vector
            if (step > 0)
            {
                for (auto index = start; index <= end; index += step)
                    result.emplace_back(data[index - 1]);
            }
            else
            {
                for (auto index = start; index >= end; index += step)
                    result.emplace_back(data[index - 1]);
            }
            return result;
        }

        template <typename T>
        std::vector<size_t> findMatlab(const std::vector<T> &data, std::function<bool(T)> func)
        {
            std::vector<size_t> res;
            res.reserve(data.size()); // reserve space
            for (size_t index = 0; index < data.size(); index++)
            {
                if (func(data[index]))
                    res.emplace_back(index);
            }
            return res;
        }

        template <typename T>
        bool anyMatlab(const std::vector<T> &data)
        {
            return std::any_of(data.begin(), data.end(), [](const T& item) { return item != 0; });
        }

        /**
         *  排序
         *
         *  @param data   待排序vector
         *
         *  @return  排序结果
         */
        template <typename T>
        std::vector<T> sort(const std::vector<T> &data)
        {
            auto res = data;
            std::sort(res.begin(), res.end());
            return res;
        }

        /**
         *  求复数的共轭
         *
         *  @param data   输入
         *
         *  @return 返回结果result
         */
        template <typename T>
        std::vector<std::complex<T>> conj(const std::vector<std::complex<T>> &data)
        {
            std::vector<std::complex<T>> result;
            result.reserve(data.size());
            for (auto it = data.cbegin(); it != data.cend(); ++it)
                result.emplace_back(std::complex<T>((*it).real(), -(*it).imag()));
            return result;
        }

        /**
         * 将 deque 转为 vector
         * @tparam T   任意类型
         * @param data deque
         * @return 返回结果
         */
        template <typename T>
        std::vector<T> toVector(const std::deque<T> &data)
        {
            std::vector<T> res;
            res.assign(data.cbegin(), data.cend());
            return res;
        }

        /**
         * 将 vector 内的元素进行 static_cast
         * @tparam T   目标类型
         * @tparam F   原类型
         * @param data 待转换的vector
         * @return     转换结果
         */
        template <typename T, typename F>
        std::vector<T> cast(const std::vector<F> &data)
        {
            std::vector<T> res;
            res.reserve(data.size());
            for (auto &&item : data)
                res.emplace_back(static_cast<T>(item));
            return res;
        }

        template <typename T>
        T stdv(std::vector<T> &data, int ddof = 0)
        {
            static_assert(std::is_floating_point<T>::value, "T must float type!");

            auto me = mean(data);
            T sum = 0.;
            for (auto item : data)
            {
                auto tmp = item - me;
                sum += (tmp * tmp);
            }
            return sqrt(sum / (data.size() - ddof));
        }

        template <typename T>
        T cov(const std::vector<T> &x, const std::vector<T> &y)
        {
            static_assert(std::is_floating_point<T>::value, "T must float type!");

            auto xMean = mean(x);
            auto yMean = mean(y);

            auto len = x.size() == y.size() ? x.size() : std::min(x.size(), y.size());
            double sums = 0;
            for (size_t i = 0; i < len; i++)
            {
                sums += (x.at(i) - xMean) * (y.at(i) - yMean);
            }
            return sums / len;
        }

        /**
         * 模拟numpy中np.var() 函数方差
         * @tparam T   浮点型
         * @param data 待计算数组
         * @return 方差
         */
        template <typename T>
        T var(const std::vector<T> &data)
        {
            static_assert(std::is_floating_point<T>::value, "T must float type!");

            auto m = mean(data);
            T var = 0.0;
            for (auto item : data)
            {
                auto tmp = item - m;
                var += (tmp * tmp);
            }
            return var / data.size();
        }

        /// @brief 数组中最小值的索引
        /// @tparam T 
        /// @param data 
        /// @return 
        template <typename T>
        size_t argmin(const std::vector<T> &data)
        {
            auto result = std::min_element(data.begin(), data.end());
            
            return std::distance(data.begin(), result);
        }

        template <typename T>
        T median(std::vector<T> &list1) {
            std::sort(list1.begin(), list1.end());
            int n = list1.size();
            T median;
            if (n % 2 == 0) {
                median = (list1[n / 2 - 1] + list1[n / 2]) / 2;
            } else {
                median = list1[n / 2];
            }
            return median;
        }

        template <typename T>
        T interp(T x, const std::vector<T>& xp, const std::vector<T>& fp) {
            auto it = std::lower_bound(xp.begin(), xp.end(), x);
            if (it == xp.begin()) {
                return fp.front();
            }
            if (it == xp.end()) {
                return fp.back();
            }
            auto i = it - xp.begin();
            double x0 = xp[i - 1], x1 = xp[i];
            double y0 = fp[i - 1], y1 = fp[i];
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
        }


        /// @brief 数组中最大值的索引
        /// @tparam T 
        /// @param data 
        /// @return 
        template <typename T>
        size_t argmax(const std::vector<T> &data)
        {
            auto result = std::max_element(data.begin(), data.end());
            
            return std::distance(data.begin(), result);
        }


        template <typename T>
        class Polynomial {
        public:
            Polynomial(std::vector<T> coefficients) : coefficients_(coefficients) {}

            T operator()(T value) const {
                T result = 0.0;
                auto coefSize = coefficients_.size();
                for (int i = 0; i < coefSize; ++i) {
                    result += coefficients_[coefSize-1-i] * std::pow(value, i);
                }
                return result;
            }

        private:
            std::vector<T> coefficients_;
        };

    }
}

#endif // ENTEREEG_NUMPY_BASIC_H
